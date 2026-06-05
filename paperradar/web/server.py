from __future__ import annotations

from copy import deepcopy
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

from ..config import AppConfig, DEFAULT_NOTIFICATIONS, DEFAULT_SETTINGS, DEFAULT_ZOTERO, read_yaml
from ..notifications import test_channel
from ..runner import Runner
from ..sources.rss import discover_feed_candidates


def run_web(root: Path | str = ".", host: str = "127.0.0.1", port: int = 8766) -> None:
    root_path = Path(root).resolve()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self.send_html(index_html(root_path))
            elif parsed.path == "/api/state":
                config = AppConfig(root_path)
                runner = Runner(root_path)
                try:
                    reports = runner.storage.recent_runs(20)
                finally:
                    runner.close()
                self.send_json(
                    {
                        "topics": [topic.__dict__ for topic in config.topics()],
                        "subscriptions": [sub.__dict__ for sub in config.subscriptions()],
                        "notifications": config.notifications.get("channels", {}),
                        "runtime": runtime_state(config),
                        "reports": reports,
                    }
                )
            else:
                self.send_error(404)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/api/run":
                query = parse_qs(parsed.query)
                sid = (query.get("subscription") or [""])[0]
                no_push = (query.get("no_push") or ["true"])[0].lower() != "false"
                runner = Runner(root_path)
                try:
                    subs = runner.config.subscriptions()
                    if sid:
                        subs = [sub for sub in subs if sub.id == sid]
                    if not subs:
                        self.send_json({"ok": False, "error": "subscription not found"}, status=404)
                        return
                    try:
                        result = runner.run_subscription(subs[0], no_push=no_push)
                    except RuntimeError as exc:
                        self.send_json({"ok": False, "error": str(exc)}, status=400)
                        return
                    self.send_json({"ok": True, "run_id": result.run_id, "report": result.report_markdown})
                finally:
                    runner.close()
            elif parsed.path == "/api/topic":
                payload = self.read_json()
                try:
                    topic_id = upsert_topic(root_path, payload)
                except ValueError as exc:
                    self.send_json({"ok": False, "error": str(exc)}, status=400)
                    return
                self.send_json({"ok": True, "id": topic_id})
            elif parsed.path == "/api/subscription":
                payload = self.read_json()
                try:
                    subscription_id = upsert_subscription(root_path, payload)
                except ValueError as exc:
                    self.send_json({"ok": False, "error": str(exc)}, status=400)
                    return
                self.send_json({"ok": True, "id": subscription_id})
            elif parsed.path == "/api/notification":
                payload = self.read_json()
                try:
                    channel = upsert_notification(root_path, payload)
                except ValueError as exc:
                    self.send_json({"ok": False, "error": str(exc)}, status=400)
                    return
                self.send_json({"ok": True, "channel": channel})
            elif parsed.path == "/api/test-notification":
                query = parse_qs(parsed.query)
                channel = (query.get("channel") or [""])[0]
                if not channel:
                    self.send_json({"ok": False, "error": "channel is required"}, status=400)
                    return
                config = AppConfig(root_path)
                result = test_channel(config.notifications, channel)
                self.send_json({"ok": result.ok, "channel": result.channel, "message": result.message}, status=200 if result.ok else 400)
            elif parsed.path == "/api/settings":
                payload = self.read_json()
                try:
                    upsert_runtime_settings(root_path, payload)
                except ValueError as exc:
                    self.send_json({"ok": False, "error": str(exc)}, status=400)
                    return
                self.send_json({"ok": True})
            elif parsed.path == "/api/rss-discover":
                payload = self.read_json()
                candidates = discover_feed_candidates(str(payload.get("journal") or ""), str(payload.get("homepage_url") or ""))
                self.send_json({"ok": True, "candidates": [candidate.__dict__ for candidate in candidates]})
            else:
                self.send_error(404)

        def do_DELETE(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            try:
                if parsed.path == "/api/topic":
                    topic_id = (query.get("id") or [""])[0]
                    cascade = (query.get("cascade") or ["false"])[0].lower() == "true"
                    delete_topic(root_path, topic_id, cascade=cascade)
                    self.send_json({"ok": True, "id": topic_id})
                elif parsed.path == "/api/subscription":
                    sub_id = (query.get("id") or [""])[0]
                    delete_subscription(root_path, sub_id)
                    self.send_json({"ok": True, "id": sub_id})
                elif parsed.path == "/api/notification":
                    channel = (query.get("channel") or [""])[0]
                    reset_notification(root_path, channel)
                    self.send_json({"ok": True, "channel": channel})
                else:
                    self.send_error(404)
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)

        def read_json(self) -> dict:
            length = int(self.headers.get("Content-Length") or "0")
            if length <= 0:
                return {}
            data = self.rfile.read(length).decode("utf-8")
            payload = json.loads(data)
            return payload if isinstance(payload, dict) else {}

        def send_json(self, payload: dict, status: int = 200) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def send_html(self, content: str) -> None:
            data = content.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"PaperRadar Web UI: http://{host}:{port}/")
    server.serve_forever()


def upsert_topic(root: Path, payload: dict) -> str:
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("topic name is required")
    topic_id = slug_from(str(payload.get("id") or name))
    keywords = split_text_list(payload.get("keywords"))
    path = root / "config" / "topics.yaml"
    data = read_yaml(path, {"topics": []})
    topics = data.setdefault("topics", [])
    topics[:] = [item for item in topics if item.get("id") != topic_id]
    topics.append(
        {
            "id": topic_id,
            "name": name,
            "research_question": str(payload.get("research_question") or "").strip(),
            "keywords": keywords,
            "exclude_keywords": split_text_list(payload.get("exclude_keywords")),
            "venues": split_text_list(payload.get("venues")),
            "reading_goal": str(payload.get("reading_goal") or "Find papers worth reading without being overwhelmed").strip(),
            "status": str(payload.get("status") or "active").strip(),
            "library_tags": split_text_list(payload.get("library_tags")),
        }
    )
    write_yaml(path, data)
    return topic_id


def delete_topic(root: Path, topic_id: str, cascade: bool = False) -> None:
    topic_id = str(topic_id or "").strip()
    if not topic_id:
        raise ValueError("topic id is required")
    topics_path = root / "config" / "topics.yaml"
    topics_data = read_yaml(topics_path, {"topics": []})
    topics = topics_data.setdefault("topics", [])
    if not any(item.get("id") == topic_id for item in topics):
        raise ValueError("topic not found")
    subs_path = root / "config" / "subscriptions.yaml"
    subs_data = read_yaml(subs_path, {"subscriptions": []})
    subscriptions = subs_data.setdefault("subscriptions", [])
    dependents = [item for item in subscriptions if item.get("topic_id") == topic_id]
    if dependents and not cascade:
        raise ValueError("topic has subscriptions; delete subscriptions first or use cascade")
    topics_data["topics"] = [item for item in topics if item.get("id") != topic_id]
    if cascade:
        subs_data["subscriptions"] = [item for item in subscriptions if item.get("topic_id") != topic_id]
        write_yaml(subs_path, subs_data)
    write_yaml(topics_path, topics_data)


def upsert_subscription(root: Path, payload: dict) -> str:
    topic_id = str(payload.get("topic_id") or "").strip()
    sub_type = str(payload.get("type") or "paper").strip()
    if not topic_id:
        raise ValueError("topic is required")
    topics_data = read_yaml(root / "config" / "topics.yaml", {"topics": []})
    if not any(item.get("id") == topic_id for item in topics_data.get("topics", [])):
        raise ValueError("topic not found")
    if sub_type not in {"paper", "arxiv", "journal_rss"}:
        raise ValueError("unsupported subscription type")
    modules = payload.get("report_modules")
    if not isinstance(modules, list):
        modules = ["paper_digest"]
    modules = [module for module in modules if module in {"paper_digest", "fresh_updates", "periodic_review"}]
    if not modules:
        raise ValueError("select at least one report module")
    sub_id = slug_from(str(payload.get("id") or f"{topic_id}-{sub_type}"))
    source: dict
    if sub_type == "paper":
        query = str(payload.get("query") or "").strip()
        source = {"query": query}
    elif sub_type == "arxiv":
        source = {
            "categories": split_text_list(payload.get("categories")) or ["cs.AI"],
            "query": str(payload.get("query") or "").strip(),
            "mode": str(payload.get("mode") or "daily_window").strip(),
        }
    else:
        feeds = []
        for url in split_text_list(payload.get("feed_urls")):
            feeds.append({"name": str(payload.get("journal") or "Journal RSS"), "url": url, "journal": str(payload.get("journal") or "").strip()})
        if not feeds:
            raise ValueError("at least one RSS feed url is required")
        source = {"feeds": feeds}
    path = root / "config" / "subscriptions.yaml"
    data = read_yaml(path, {"subscriptions": []})
    subscriptions = data.setdefault("subscriptions", [])
    subscriptions[:] = [item for item in subscriptions if item.get("id") != sub_id]
    subscriptions.append(
        {
            "id": sub_id,
            "topic_id": topic_id,
            "type": sub_type,
            "enabled": bool(payload.get("enabled", True)),
            "report_modules": modules,
            "schedule": str(payload.get("schedule") or "manual").strip(),
            "max_papers": int(payload.get("max_papers") or 8),
            "min_score": float(payload.get("min_score") or 0.55),
            "channels": split_text_list(payload.get("channels")),
            "source": source,
            "analysis_depth": str(payload.get("analysis_depth") or "standard").strip(),
        }
    )
    write_yaml(path, data)
    return sub_id


def delete_subscription(root: Path, sub_id: str) -> None:
    sub_id = str(sub_id or "").strip()
    if not sub_id:
        raise ValueError("subscription id is required")
    path = root / "config" / "subscriptions.yaml"
    data = read_yaml(path, {"subscriptions": []})
    subscriptions = data.setdefault("subscriptions", [])
    if not any(item.get("id") == sub_id for item in subscriptions):
        raise ValueError("subscription not found")
    data["subscriptions"] = [item for item in subscriptions if item.get("id") != sub_id]
    write_yaml(path, data)


def upsert_notification(root: Path, payload: dict) -> str:
    channel = str(payload.get("channel") or "").strip()
    if not channel:
        raise ValueError("channel is required")
    defaults = DEFAULT_NOTIFICATIONS["channels"]
    if channel not in defaults:
        raise ValueError("unsupported channel")
    path = root / "config" / "notifications.yaml"
    data = read_yaml(path, DEFAULT_NOTIFICATIONS)
    channels = data.setdefault("channels", {})
    current = dict(channels.get(channel) or defaults[channel])
    cfg = payload.get("config") if isinstance(payload.get("config"), dict) else payload
    for key, value in cfg.items():
        if key == "channel":
            continue
        if key == "enabled":
            current[key] = coerce_bool(value)
        elif key == "smtp_port":
            current[key] = int(value or 0) if str(value or "").strip() else ""
        elif key in defaults[channel]:
            current[key] = value
    channels[channel] = current
    write_yaml(path, data)
    return channel


def reset_notification(root: Path, channel: str) -> None:
    channel = str(channel or "").strip()
    defaults = DEFAULT_NOTIFICATIONS["channels"]
    if channel not in defaults:
        raise ValueError("unsupported channel")
    path = root / "config" / "notifications.yaml"
    data = read_yaml(path, DEFAULT_NOTIFICATIONS)
    data.setdefault("channels", {})[channel] = dict(defaults[channel])
    write_yaml(path, data)


def runtime_state(config: AppConfig) -> dict:
    settings = config.settings
    ranking = settings.get("ranking", {})
    llm = dict(settings.get("llm", {}))
    llm_key_set = bool(llm.pop("api_key", ""))
    embedding = dict(ranking.get("embedding", {}))
    embedding_key_set = bool(embedding.pop("api_key", ""))
    zotero = dict(config.zotero.get("zotero", {}))
    zotero_key_set = bool(zotero.pop("api_key", ""))
    sources = deepcopy(settings.get("sources", {}))
    openalex = dict(sources.get("openalex", {}))
    openalex_key_set = bool(openalex.pop("api_key", ""))
    openalex.pop("email", None)
    sources["openalex"] = {**openalex, "api_key_set": openalex_key_set}
    return {
        "llm": {**llm, "api_key_set": llm_key_set},
        "embedding": {**embedding, "api_key_set": embedding_key_set},
        "ranking": {
            "library_similarity": ranking.get("library_similarity", {}),
            "llm_candidate_limit": ranking.get("llm_candidate_limit", 32),
            "llm_analysis": ranking.get("llm_analysis", {}),
        },
        "sources": sources,
        "zotero": {**zotero, "api_key_set": zotero_key_set},
    }


def upsert_runtime_settings(root: Path, payload: dict) -> None:
    settings_path = root / "config" / "settings.yaml"
    settings = read_yaml(settings_path, deepcopy(DEFAULT_SETTINGS))

    llm_payload = payload.get("llm")
    if isinstance(llm_payload, dict):
        llm = settings.setdefault("llm", {})
        update_bool(llm, llm_payload, "enabled")
        update_text(llm, llm_payload, "api_type")
        update_text(llm, llm_payload, "base_url")
        update_text(llm, llm_payload, "model")
        update_secret(llm, llm_payload, "api_key")

    ranking = settings.setdefault("ranking", {})
    embedding_payload = payload.get("embedding")
    if isinstance(embedding_payload, dict):
        embedding = ranking.setdefault("embedding", {})
        update_bool(embedding, embedding_payload, "enabled")
        update_text(embedding, embedding_payload, "base_url")
        update_text(embedding, embedding_payload, "model")
        update_int(embedding, embedding_payload, "batch_size", minimum=1)
        update_secret(embedding, embedding_payload, "api_key")
        library_similarity = ranking.setdefault("library_similarity", {})
        mode = str(embedding_payload.get("mode") or library_similarity.get("mode") or "lexical").strip()
        if mode not in {"lexical", "embedding_api"}:
            raise ValueError("unsupported similarity mode")
        library_similarity["mode"] = mode
        if coerce_bool(embedding.get("enabled")):
            library_similarity["mode"] = "embedding_api"

    ranking_payload = payload.get("ranking")
    if isinstance(ranking_payload, dict):
        update_int(ranking, ranking_payload, "llm_candidate_limit", minimum=1)
        analysis_payload = ranking_payload.get("llm_analysis")
        if isinstance(analysis_payload, dict):
            analysis = ranking.setdefault("llm_analysis", {})
            update_bool(analysis, analysis_payload, "enabled")
            update_text(analysis, analysis_payload, "language")
            update_int(analysis, analysis_payload, "max_papers", minimum=1)

    sources_payload = payload.get("sources")
    if isinstance(sources_payload, dict):
        sources = settings.setdefault("sources", {})
        for source_name in ["openalex", "crossref", "arxiv", "journal_rss"]:
            source_payload = sources_payload.get(source_name)
            if not isinstance(source_payload, dict):
                continue
            source_cfg = sources.setdefault(source_name, {})
            update_bool(source_cfg, source_payload, "enabled")
            if source_name == "openalex":
                source_cfg.pop("email", None)
                update_secret(source_cfg, source_payload, "api_key")
            else:
                update_text(source_cfg, source_payload, "email")

    write_yaml(settings_path, settings)

    zotero_payload = payload.get("zotero")
    if isinstance(zotero_payload, dict):
        zotero_path = root / "config" / "zotero.yaml"
        zotero_data = read_yaml(zotero_path, deepcopy(DEFAULT_ZOTERO))
        zotero = zotero_data.setdefault("zotero", {})
        update_bool(zotero, zotero_payload, "enabled")
        update_text(zotero, zotero_payload, "user_id")
        update_text(zotero, zotero_payload, "group_id")
        update_secret(zotero, zotero_payload, "api_key")
        for key in ["collections", "tags", "include_path", "ignore_path"]:
            if key in zotero_payload:
                zotero[key] = split_text_list(zotero_payload.get(key))
        write_yaml(zotero_path, zotero_data)


def update_text(target: dict, payload: dict, key: str) -> None:
    if key in payload:
        target[key] = str(payload.get(key) or "").strip()


def update_secret(target: dict, payload: dict, key: str) -> None:
    value = str(payload.get(key) or "").strip()
    if value:
        target[key] = value


def update_bool(target: dict, payload: dict, key: str) -> None:
    if key in payload:
        target[key] = coerce_bool(payload.get(key))


def update_int(target: dict, payload: dict, key: str, minimum: int = 0) -> None:
    if key not in payload:
        return
    text = str(payload.get(key) or "").strip()
    if not text:
        return
    value = int(text)
    if value < minimum:
        raise ValueError(f"{key} must be >= {minimum}")
    target[key] = value


def coerce_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
    return bool(value)


def split_text_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "")
    parts = re.split(r"[\n,，]+", text)
    return [part.strip() for part in parts if part.strip()]


def slug_from(value: str) -> str:
    slug = "-".join(part for part in re.sub(r"[^0-9a-zA-Z]+", "-", value.lower()).split("-") if part)
    return slug or "item"


def write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, allow_unicode=True, sort_keys=False)


def index_html(root: Path) -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PaperRadar</title>
  <style>
    :root { --bg:#f6f6f4; --panel:#fff; --ink:#171717; --muted:#696b68; --line:#deded8; --soft:#f0f0ec; --blue:#356fac; --red:#b42318; --ok:#1f6b3a; }
    * { box-sizing:border-box; }
    body { margin:0; background:var(--bg); color:var(--ink); font-family:Inter, system-ui, -apple-system, "Segoe UI", sans-serif; }
    header { background:var(--panel); border-bottom:1px solid var(--line); padding:18px 24px; display:flex; align-items:center; justify-content:space-between; gap:16px; }
    h1 { margin:0; font-size:22px; letter-spacing:0; }
    main { max-width:1240px; margin:0 auto; padding:22px; display:grid; gap:16px; }
    section { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:16px; }
    h2 { margin:0 0 12px; font-size:17px; letter-spacing:0; }
    h3 { margin:0 0 10px; font-size:15px; letter-spacing:0; }
    .tabs { display:flex; gap:8px; flex-wrap:wrap; }
    .tab { background:var(--soft); color:var(--ink); }
    .tab.active { background:#111; color:#fff; }
    .view { display:none; gap:16px; }
    .view.active { display:grid; }
    .split { display:grid; grid-template-columns:minmax(300px, 430px) 1fr; gap:16px; align-items:start; }
    .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:12px; }
    .stack { display:grid; gap:12px; }
    .form-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:12px; }
    label { display:block; color:var(--ink); font-size:13px; font-weight:700; }
    label > input, label > textarea, label > select { display:block; margin-top:7px; }
    input, textarea, select { width:100%; border:1px solid var(--line); border-radius:7px; padding:9px 10px; background:#fff; color:var(--ink); font:inherit; font-weight:400; }
    textarea { min-height:72px; resize:vertical; }
    details { border:1px solid var(--line); border-radius:8px; padding:11px; background:#fff; }
    summary { cursor:pointer; font-weight:700; }
    button { border:0; border-radius:7px; padding:8px 12px; background:#111; color:#fff; font-weight:700; cursor:pointer; }
    button.secondary { background:var(--soft); color:#222; }
    button.danger { background:#fff0ee; color:var(--red); border:1px solid #f0b8b0; }
    button:disabled { opacity:.55; cursor:not-allowed; }
    .card { border:1px solid var(--line); border-radius:8px; padding:13px; background:#fff; display:grid; gap:9px; }
    .row { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
    .between { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; }
    .muted { color:var(--muted); }
    .hint { color:var(--muted); font-size:12px; line-height:1.45; }
    .required, .optional { display:inline-block; width:max-content; margin-left:6px; padding:2px 7px; border-radius:999px; font-size:11px; line-height:1.2; font-weight:800; vertical-align:middle; }
    .required { color:var(--red); background:#fff0ee; border:1px solid #f0b8b0; }
    .optional { color:#555; background:#f2f2ee; border:1px solid var(--line); }
    .pill { display:inline-block; padding:3px 7px; border-radius:999px; background:#e9f2fb; color:#245a94; font-size:12px; }
    .off { background:#f1f1ed; color:#666; }
    .ok { background:#e9f7ef; color:var(--ok); }
    .danger-text { color:var(--red); }
    pre { white-space:pre-wrap; background:#050505; color:#f5f5f5; border-radius:8px; padding:14px; max-height:460px; overflow:auto; }
    .checkbox-line { display:flex; align-items:center; gap:7px; color:var(--ink); font-size:13px; font-weight:500; }
    .checkbox-line input { width:auto; }
    .check-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr)); gap:8px; margin-top:6px; }
    .channel-list { display:grid; gap:10px; }
    .channel-card textarea { min-height:50px; }
    .doc-link { color:var(--blue); font-size:12px; text-decoration:none; font-weight:700; }
    .doc-link:hover { text-decoration:underline; }
    .settings-layout { display:grid; grid-template-columns:minmax(0, 1fr) minmax(280px, 340px); gap:16px; align-items:start; }
    .settings-primary { display:grid; gap:16px; }
    .settings-grid { display:grid; grid-template-columns:repeat(2,minmax(260px,1fr)); gap:16px; }
    .settings-panel { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:16px; display:grid; gap:10px; }
    .settings-side { display:grid; gap:12px; }
    .modal-backdrop { position:fixed; inset:0; background:rgba(0,0,0,.34); display:flex; align-items:center; justify-content:center; padding:18px; z-index:20; }
    .modal-backdrop.hidden { display:none; }
    .modal { width:min(880px, 96vw); max-height:92vh; overflow:auto; background:var(--panel); border-radius:8px; border:1px solid var(--line); padding:16px; box-shadow:0 18px 60px rgba(0,0,0,.25); }
    .source-field.hidden { display:none; }
    @media (max-width: 920px) { .settings-layout, .settings-grid { grid-template-columns:1fr; } }
    @media (max-width: 820px) { .split { grid-template-columns:1fr; } header { align-items:flex-start; flex-direction:column; } }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>PaperRadar</h1>
      <div class="muted">研究主题、订阅、推送与报告管理</div>
    </div>
    <div class="tabs">
      <button class="tab active" onclick="showTab('topics', this)">主题</button>
      <button class="tab" onclick="showTab('subscriptions', this)">订阅</button>
      <button class="tab" onclick="showTab('notifications', this)">推送</button>
      <button class="tab" onclick="showTab('settings', this)">设置</button>
      <button class="tab" onclick="showTab('reports', this)">报告</button>
    </div>
  </header>
  <main>
    <section id="view-topics" class="view active">
      <div class="split">
        <div>
          <h2>新增研究主题</h2>
          <div class="stack">
            <label>主题名称 <span class="required">必填</span><input id="topicName" placeholder="LLM Literature Recommendation"></label>
            <label>研究问题 <span class="required">必填</span><input id="topicQuestion" placeholder="我想长期跟踪什么研究问题？"></label>
            <label>关键词 <span class="required">必填</span><textarea id="topicKeywords" placeholder="large language model, literature recommendation"></textarea></label>
            <details>
              <summary>选填信息</summary>
              <div class="form-grid" style="margin-top:12px">
                <label>排除关键词 <span class="optional">选填</span><textarea id="topicExclude"></textarea></label>
                <label>关注期刊/会议 <span class="optional">选填</span><textarea id="topicVenues"></textarea></label>
              </div>
            </details>
            <details>
              <summary>高级设置</summary>
              <div class="form-grid" style="margin-top:12px">
                <label>自定义 ID <span class="optional">选填</span><input id="topicId" placeholder="llm-literature"></label>
                <label>Zotero 标签 <span class="optional">选填</span><textarea id="topicLibraryTags"></textarea></label>
                <label>阅读目标 <span class="optional">选填</span><textarea id="topicGoal"></textarea></label>
              </div>
            </details>
          </div>
          <div class="row" style="margin-top:12px">
            <button onclick="createTopic()">新增主题</button>
            <button class="secondary" onclick="clearTopicForm()">清空</button>
          </div>
        </div>
        <div>
          <h2>已有主题</h2>
          <div id="topicsList" class="grid"></div>
        </div>
      </div>
    </section>

    <section id="view-subscriptions" class="view">
      <div class="split">
        <div>
          <h2>新增订阅</h2>
          <div class="stack">
            <label>研究主题 <span class="required">必填</span><select id="subTopic"></select></label>
            <label>订阅类型 <span class="required">必填</span><select id="subType" onchange="syncSourceFields()"><option value="paper">常规论文精选</option><option value="arxiv">arXiv 新文追踪</option><option value="journal_rss">期刊 RSS 新文追踪</option></select></label>
            <div id="paperFields" class="source-field">
              <label>补充检索式 <span class="optional">选填</span><input id="subQuery" placeholder="literature recommendation ranking"></label>
            </div>
            <div id="arxivFields" class="source-field hidden">
              <div class="muted">arXiv 分类 <span class="optional">选填，默认人工智能</span></div>
              <div id="subCategoryChoices" class="check-grid"></div>
              <label>arXiv 检索词 <span class="optional">选填</span><input id="subArxivQuery" placeholder="agentic retrieval"></label>
            </div>
            <div id="journalFields" class="source-field hidden stack">
              <label>RSS 链接 <span class="required">必填</span><textarea id="subFeeds" placeholder="https://www.nature.com/nature.rss"></textarea></label>
              <label>期刊名 <span class="optional">选填</span><input id="subJournal" placeholder="Nature"></label>
              <details>
                <summary>帮助查找期刊 RSS</summary>
                <div class="form-grid" style="margin-top:12px">
                  <label>期刊主页 URL <span class="optional">选填</span><input id="rssHomepage" placeholder="https://www.nature.com/nature/"></label>
                </div>
                <div class="row" style="margin-top:10px"><button class="secondary" onclick="discoverRss()">查找 RSS</button></div>
                <div id="rssResults" class="stack" style="margin-top:10px"></div>
              </details>
            </div>
            <div>
              <div class="muted">报告模块 <span class="required">必选至少一项</span></div>
              <div id="subModules" class="check-grid"></div>
            </div>
            <div>
              <div class="muted">推送渠道 <span class="optional">选填</span></div>
              <div id="subChannelChoices" class="check-grid"></div>
            </div>
            <details>
              <summary>选填参数</summary>
              <div class="form-grid" style="margin-top:12px">
                <label>运行计划 <span class="optional">选填</span><select id="subSchedule"><option value="manual">手动运行</option><option value="daily 07:00">每天 07:00</option><option value="daily 09:00">每天 09:00</option><option value="weekly monday 09:00">每周一 09:00</option></select></label>
                <label>最多论文数 <span class="optional">选填</span><input id="subMax" type="number" value="8" min="1" max="100"></label>
              </div>
            </details>
            <details>
              <summary>高级设置</summary>
              <div class="form-grid" style="margin-top:12px">
                <label>自定义 ID <span class="optional">选填</span><input id="subId" placeholder="daily-paper-digest"></label>
                <label>最低推荐分 <span class="optional">选填</span><input id="subMin" type="number" value="0.55" step="0.05" min="0" max="1"></label>
                <label>分析深度 <span class="optional">选填</span><select id="subDepth"><option value="standard">标准分析</option><option value="brief">简短摘要</option><option value="deep">深度分析</option></select></label>
                <label>arXiv 范围 <span class="optional">选填</span><select id="subMode"><option value="daily_window">每日新增窗口</option><option value="latest">最近更新</option><option value="category_latest">分类最新文章</option></select></label>
              </div>
            </details>
          </div>
          <div class="row" style="margin-top:12px">
            <button onclick="createSubscription()">新增订阅</button>
            <button class="secondary" onclick="clearSubscriptionForm()">清空</button>
          </div>
        </div>
        <div>
          <h2>已有订阅</h2>
          <div id="subscriptionsList" class="grid"></div>
        </div>
      </div>
    </section>

    <section id="view-notifications" class="view">
      <div class="between">
        <div>
          <h2>推送渠道</h2>
        </div>
        <button class="secondary" onclick="loadState()">刷新</button>
      </div>
      <div id="notificationsList" class="channel-list"></div>
    </section>

    <section id="view-settings" class="view">
      <div class="between">
        <h2>设置</h2>
        <button onclick="saveSettings()">保存设置</button>
      </div>
      <div class="settings-layout">
        <div class="settings-primary">
          <div class="settings-grid">
            <div class="settings-panel">
              <h3>LLM 分析</h3>
              <label class="checkbox-line"><input type="checkbox" id="llmEnabled">启用 LLM 分析</label>
              <div class="form-grid" style="margin-top:10px">
                <label>密钥 <span class="optional">选填；启用 LLM 时必填</span><input id="llmApiKey" type="password"></label>
                <label>接口地址 <span class="optional">选填</span><input id="llmBaseUrl"></label>
                <label>模型 <span class="optional">选填</span><input id="llmModel"></label>
                <label>接口类型 <span class="optional">选填</span><select id="llmApiType"><option value="openai_chat">OpenAI 兼容聊天接口</option></select></label>
              </div>
            </div>
            <div class="settings-panel">
              <h3>Embedding 预筛</h3>
              <label class="checkbox-line"><input type="checkbox" id="embeddingEnabled">启用向量相似度预筛</label>
              <div class="form-grid" style="margin-top:10px">
                <label>相似度模式 <span class="optional">选填</span><select id="embeddingMode"><option value="lexical">关键词相似度</option><option value="embedding_api">向量相似度</option></select></label>
                <label>密钥 <span class="optional">选填</span><input id="embeddingApiKey" type="password"></label>
                <label>接口地址 <span class="optional">选填</span><input id="embeddingBaseUrl"></label>
                <label>模型 <span class="optional">选填</span><input id="embeddingModel"></label>
                <label>批量大小 <span class="optional">选填</span><input id="embeddingBatch" type="number" min="1"></label>
              </div>
            </div>
          </div>
          <div class="settings-panel">
              <h3>Zotero 文献库</h3>
              <label class="checkbox-line"><input type="checkbox" id="zoteroEnabled">启用 Zotero 文献库相关性</label>
              <div class="form-grid" style="margin-top:10px">
                <label>个人用户 ID <span class="optional">个人库填</span><input id="zoteroUserId"></label>
                <label>群组 ID <span class="optional">群组库填</span><input id="zoteroGroupId"></label>
                <label>密钥 <span class="optional">启用 Zotero 时必填</span><input id="zoteroApiKey" type="password"></label>
                <label>收藏夹/Collection <span class="optional">选填</span><textarea id="zoteroCollections"></textarea></label>
                <label>标签 <span class="optional">选填</span><textarea id="zoteroTags"></textarea></label>
                <label>包含路径 <span class="optional">选填</span><textarea id="zoteroInclude"></textarea></label>
                <label>忽略路径 <span class="optional">选填</span><textarea id="zoteroIgnore"></textarea></label>
              </div>
          </div>
        </div>
        <div class="settings-side">
          <div class="settings-panel">
            <h3>常规论文源</h3>
            <label class="checkbox-line"><input type="checkbox" id="openalexEnabled">OpenAlex</label>
            <label>OpenAlex API Key <span class="required">启用 OpenAlex 时必填</span><input id="openalexApiKey" type="password"></label>
            <div><a class="doc-link" href="https://docs.openalex.org/how-to-use-the-api/rate-limits-and-authentication" target="_blank" rel="noopener noreferrer">OpenAlex API Key 官方说明</a></div>
            <label class="checkbox-line" style="margin-top:10px"><input type="checkbox" id="crossrefEnabled">Crossref</label>
            <label>Crossref 邮箱 <span class="optional">选填，用于 User-Agent</span><input id="crossrefEmail"></label>
          </div>
          <div class="settings-panel">
            <h3>新文来源</h3>
            <label class="checkbox-line"><input type="checkbox" id="arxivEnabled">arXiv</label>
            <label class="checkbox-line"><input type="checkbox" id="journalRssEnabled">期刊 RSS</label>
          </div>
        </div>
      </div>
    </section>

    <section id="view-reports" class="view">
      <div class="between">
        <h2>运行与报告</h2>
        <div class="row">
          <button onclick="runFirst()">运行第一个启用订阅</button>
          <button class="secondary" onclick="loadState()">刷新</button>
        </div>
      </div>
      <div id="reportsList" class="grid"></div>
      <pre id="output">Ready.</pre>
    </section>
  </main>

  <div id="modalBackdrop" class="modal-backdrop hidden">
    <div class="modal">
      <div class="between">
        <h2 id="modalTitle">编辑</h2>
        <button class="secondary" onclick="closeModal()">关闭</button>
      </div>
      <div id="modalBody" class="stack"></div>
      <div id="modalActions" class="row" style="margin-top:14px"></div>
    </div>
  </div>

  <script>
    let state = { topics: [], subscriptions: [], notifications: {}, runtime: {}, reports: [] };
    const modules = [['paper_digest','论文精选'], ['fresh_updates','新文追踪'], ['periodic_review','周期综述']];
    const arxivCategories = [
      ['cs.AI', '人工智能'],
      ['cs.CL', '计算语言学'],
      ['cs.LG', '机器学习'],
      ['stat.ML', '统计机器学习'],
      ['cs.CV', '计算机视觉'],
      ['cs.IR', '信息检索'],
      ['cs.HC', '人机交互'],
      ['cs.RO', '机器人']
    ];
    const channelFields = {
      feishu: [['webhook_url','机器人 Webhook 地址','textarea','enabled_required']],
      email: [['from','发件邮箱','text','enabled_required'],['password','SMTP 授权码或密码','password','enabled_required'],['to','收件邮箱','text','enabled_required'],['smtp_server','SMTP 服务器','text','enabled_required'],['smtp_port','SMTP 端口','number','enabled_required']],
      dingtalk: [['webhook_url','机器人 Webhook 地址','textarea','enabled_required']],
      wework: [['webhook_url','群机器人 Webhook 地址','textarea','enabled_required'],['msg_type','消息格式','select','optional']],
      generic: [['webhook_url','接收端 Webhook 地址','textarea','enabled_required']],
      telegram: [['bot_token','机器人 Token','password','enabled_required'],['chat_id','接收 Chat ID','text','enabled_required']],
      ntfy: [['server_url','服务器地址','text','enabled_required'],['topic','主题名','text','enabled_required'],['token','访问 Token','password','optional']],
      bark: [['url','Bark 推送地址','textarea','enabled_required']],
      slack: [['webhook_url','Incoming Webhook 地址','textarea','enabled_required']]
    };
    const channelLabels = { feishu:'飞书/Lark', email:'邮件 SMTP', dingtalk:'钉钉', wework:'企业微信', generic:'通用 Webhook', telegram:'Telegram', ntfy:'ntfy', bark:'Bark', slack:'Slack' };
    const channelDocs = {
      feishu: ['飞书自定义机器人官方说明', 'https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot'],
      email: ['Gmail 应用专用密码说明', 'https://support.google.com/mail/answer/185833'],
      dingtalk: ['钉钉自定义机器人官方说明', 'https://open.dingtalk.com/document/dingstart/custom-bot-creation-and-installation'],
      wework: ['企业微信群机器人官方说明', 'https://developer.work.weixin.qq.com/document/path/91770'],
      generic: ['按接收端 Webhook 文档配置', ''],
      telegram: ['Telegram Bot 官方说明', 'https://core.telegram.org/bots'],
      ntfy: ['ntfy 发送消息官方说明', 'https://docs.ntfy.sh/publish/'],
      bark: ['Bark 官方说明', 'https://bark.day.app/#/tutorial'],
      slack: ['Slack Incoming Webhooks 官方说明', 'https://api.slack.com/messaging/webhooks']
    };
    const channelSelectOptions = {
      msg_type: [['markdown', 'Markdown 格式'], ['text', '纯文本']]
    };

    function esc(value) {
      return String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
    }
    function csv(value) {
      return Array.isArray(value) ? value.join(', ') : String(value || '');
    }
    function channelKeys() {
      return Object.keys(channelFields);
    }
    function showTab(name, button) {
      document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
      document.getElementById(`view-${name}`).classList.add('active');
      document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      button.classList.add('active');
    }
    async function loadState() {
      const res = await fetch('/api/state');
      state = await res.json();
      renderTopics();
      renderChannelChoices('subChannelChoices', []);
      renderModules('subModules', ['paper_digest']);
      renderArxivCategories('subCategoryChoices', []);
      renderSubscriptions();
      renderNotifications();
      renderSettings();
      renderReports();
      syncSourceFields();
    }

    function requireValue(id, label) {
      const value = document.getElementById(id).value.trim();
      if (!value) throw new Error(`${label} 是必填项`);
      return value;
    }
    function showError(error) {
      document.getElementById('output').textContent = error.message || String(error);
      document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
      document.getElementById('view-reports').classList.add('active');
      document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      document.querySelectorAll('.tab')[4].classList.add('active');
    }

    function renderTopics() {
      document.getElementById('subTopic').innerHTML = state.topics.map(t => `<option value="${esc(t.id)}">${esc(t.name)}</option>`).join('');
      document.getElementById('topicsList').innerHTML = state.topics.map(t => `
        <div class="card">
          <div class="between"><strong>${esc(t.name)}</strong><span class="pill ${t.status === 'active' ? 'ok' : 'off'}">${esc(statusLabel(t.status))}</span></div>
          <div class="muted">${esc(t.id)}</div>
          <div>${esc(t.research_question || '')}</div>
          <div class="muted">${esc(csv(t.keywords))}</div>
          <div class="row">
            <button class="secondary" onclick="openTopicEdit('${esc(t.id)}')">编辑</button>
            <button class="secondary" onclick="toggleTopic('${esc(t.id)}')">${t.status === 'active' ? '暂停' : '恢复'}</button>
            <button class="danger" onclick="deleteTopic('${esc(t.id)}')">删除</button>
          </div>
        </div>`).join('');
    }
    async function createTopic() {
      try {
        const payload = {
          id: document.getElementById('topicId').value,
          name: requireValue('topicName', '主题名称'),
          status: 'active',
          research_question: requireValue('topicQuestion', '研究问题'),
          keywords: requireValue('topicKeywords', '关键词'),
          exclude_keywords: document.getElementById('topicExclude').value,
          venues: document.getElementById('topicVenues').value,
          library_tags: document.getElementById('topicLibraryTags').value,
          reading_goal: document.getElementById('topicGoal').value
        };
        await postJson('/api/topic', payload);
        clearTopicForm();
        await loadState();
      } catch (error) { showError(error); }
    }
    function clearTopicForm() {
      ['topicId','topicName','topicQuestion','topicKeywords','topicExclude','topicVenues','topicLibraryTags','topicGoal'].forEach(id => document.getElementById(id).value = '');
    }
    function topicPayloadFromState(t, overrides = {}) {
      return {
        id: t.id,
        name: t.name,
        status: t.status || 'active',
        research_question: t.research_question || '',
        keywords: t.keywords || [],
        exclude_keywords: t.exclude_keywords || [],
        venues: t.venues || [],
        library_tags: t.library_tags || [],
        reading_goal: t.reading_goal || '',
        ...overrides
      };
    }
    async function toggleTopic(id) {
      const t = state.topics.find(item => item.id === id);
      if (!t) return;
      await postJson('/api/topic', topicPayloadFromState(t, { status: t.status === 'active' ? 'paused' : 'active' }));
      await loadState();
    }
    function openTopicEdit(id) {
      const t = state.topics.find(item => item.id === id);
      if (!t) return;
      openModal('编辑研究主题', `
        <div class="form-grid">
          <label>主题名称 <span class="required">必填</span><input id="editTopicName" value="${esc(t.name)}"></label>
          <label>状态<select id="editTopicStatus"><option value="active">正常推送</option><option value="paused">暂停推送</option></select></label>
          <label>研究问题 <span class="required">必填</span><input id="editTopicQuestion" value="${esc(t.research_question)}"></label>
          <label>关键词 <span class="required">必填</span><textarea id="editTopicKeywords">${esc(csv(t.keywords))}</textarea></label>
          <label>排除关键词 <span class="optional">选填</span><textarea id="editTopicExclude">${esc(csv(t.exclude_keywords))}</textarea></label>
          <label>关注期刊/会议 <span class="optional">选填</span><textarea id="editTopicVenues">${esc(csv(t.venues))}</textarea></label>
          <label>Zotero 标签 <span class="optional">高级</span><textarea id="editTopicLibraryTags">${esc(csv(t.library_tags))}</textarea></label>
          <label>阅读目标 <span class="optional">高级</span><textarea id="editTopicGoal">${esc(t.reading_goal)}</textarea></label>
        </div>`,
        `<button onclick="saveTopicEdit('${esc(t.id)}')">保存修改</button>`
      );
      document.getElementById('editTopicStatus').value = t.status || 'active';
    }
    async function saveTopicEdit(id) {
      try {
        await postJson('/api/topic', {
          id,
          name: requireValue('editTopicName', '主题名称'),
          status: document.getElementById('editTopicStatus').value,
          research_question: requireValue('editTopicQuestion', '研究问题'),
          keywords: requireValue('editTopicKeywords', '关键词'),
          exclude_keywords: document.getElementById('editTopicExclude').value,
          venues: document.getElementById('editTopicVenues').value,
          library_tags: document.getElementById('editTopicLibraryTags').value,
          reading_goal: document.getElementById('editTopicGoal').value
        });
        closeModal();
        await loadState();
      } catch (error) { showError(error); }
    }
    async function deleteTopic(id) {
      if (!confirm(`删除主题 ${id}？关联订阅也会删除。`)) return;
      await deleteJson(`/api/topic?id=${encodeURIComponent(id)}&cascade=true`);
      await loadState();
    }

    function renderModules(containerId, selected) {
      document.getElementById(containerId).innerHTML = modules.map(([value, label]) => `
        <label class="checkbox-line"><input type="checkbox" value="${value}" ${selected.includes(value) ? 'checked' : ''}>${label}</label>`).join('');
    }
    function renderArxivCategories(containerId, selected) {
      const selectedSet = new Set(selected && selected.length ? selected : ['cs.AI']);
      document.getElementById(containerId).innerHTML = arxivCategories.map(([value, label]) => `
        <label class="checkbox-line"><input type="checkbox" value="${value}" ${selectedSet.has(value) ? 'checked' : ''}>${label}</label>`).join('');
    }
    function selectedChecks(containerId) {
      return Array.from(document.querySelectorAll(`#${containerId} input[type="checkbox"]:checked`)).map(input => input.value);
    }
    function renderChannelChoices(containerId, selected) {
      document.getElementById(containerId).innerHTML = channelKeys().map(channel => `
        <label class="checkbox-line"><input type="checkbox" value="${channel}" ${selected.includes(channel) ? 'checked' : ''}>${esc(channelLabels[channel])}</label>`).join('');
    }
    function renderSubscriptions() {
      document.getElementById('subscriptionsList').innerHTML = state.subscriptions.map(s => `
        <div class="card">
          <div class="between"><strong>${esc(s.id)}</strong><span class="pill ${s.enabled ? 'ok' : 'off'}">${s.enabled ? '已启用' : '已停用'}</span></div>
          <div>${esc(typeLabel(s.type))} · ${esc(topicName(s.topic_id))}</div>
          <div class="muted">${esc(sourceSummary(s))}</div>
          <div class="muted">报告：${esc(moduleLabels(s.report_modules))}</div>
          <div class="muted">渠道：${esc(channelSummary(s.channels))}</div>
          <div class="row">
            <button onclick="runSub('${esc(s.id)}')">运行</button>
            <button class="secondary" onclick="openSubscriptionEdit('${esc(s.id)}')">编辑</button>
            <button class="secondary" onclick="toggleSubscription('${esc(s.id)}')">${s.enabled ? '停用' : '启用'}</button>
            <button class="danger" onclick="deleteSubscription('${esc(s.id)}')">删除</button>
          </div>
        </div>`).join('');
    }
    function typeLabel(type) {
      return {paper:'常规论文精选', arxiv:'arXiv 新文追踪', journal_rss:'期刊 RSS 新文追踪'}[type] || type;
    }
    function topicName(id) {
      return (state.topics.find(t => t.id === id) || {}).name || id;
    }
    function moduleLabels(values) {
      return (values || []).map(v => (modules.find(m => m[0] === v) || [v, v])[1]).join(', ');
    }
    function channelSummary(values) {
      return values && values.length ? values.map(v => channelLabels[v] || v).join(', ') : '所有已启用渠道';
    }
    function statusLabel(value) {
      return value === 'paused' ? '暂停推送' : '正常推送';
    }
    function depthLabel(value) {
      return { brief:'简短摘要', standard:'标准分析', deep:'深度分析' }[value] || '标准分析';
    }
    function arxivModeLabel(value) {
      return { daily_window:'每日新增窗口', latest:'最近更新', category_latest:'分类最新文章' }[value] || '每日新增窗口';
    }
    function categoryLabels(values) {
      return (values || []).map(value => (arxivCategories.find(item => item[0] === value) || [value, value])[1]).join(', ');
    }
    function sourceSummary(s) {
      if (s.type === 'paper') return s.source?.query || '按研究主题自动检索';
      if (s.type === 'arxiv') return `分类：${categoryLabels(s.source?.categories || ['cs.AI'])}${s.source?.query ? `；检索词：${s.source.query}` : ''}`;
      return (s.source?.feeds || []).map(feed => feed.journal || feed.name || feed.url).join(', ');
    }
    function syncSourceFields() {
      const type = document.getElementById('subType').value;
      document.getElementById('paperFields').classList.toggle('hidden', type !== 'paper');
      document.getElementById('arxivFields').classList.toggle('hidden', type !== 'arxiv');
      document.getElementById('journalFields').classList.toggle('hidden', type !== 'journal_rss');
    }
    async function createSubscription() {
      try {
        const payload = subscriptionPayloadFromForm('');
        await postJson('/api/subscription', payload);
        clearSubscriptionForm();
        await loadState();
      } catch (error) { showError(error); }
    }
    function subscriptionPayloadFromForm(prefix) {
      const get = id => document.getElementById(prefix + id);
      const type = get('subType').value;
      const payload = {
        id: prefix ? '' : get('subId').value,
        topic_id: get('subTopic').value,
        type,
        schedule: get('subSchedule').value || 'manual',
        max_papers: Number(get('subMax').value || 8),
        min_score: Number(get('subMin').value || 0.55),
        channels: selectedChecks(prefix + 'subChannelChoices'),
        report_modules: selectedChecks(prefix + 'subModules'),
        enabled: prefix ? get('subEnabled').checked : true,
        analysis_depth: get('subDepth').value,
        mode: get('subMode').value,
        query: '',
        categories: [],
        journal: '',
        feed_urls: ''
      };
      if (!payload.report_modules.length) throw new Error('报告模块必选至少一项');
      if (type === 'paper') payload.query = get('subQuery').value;
      if (type === 'arxiv') {
        payload.categories = selectedChecks(prefix + 'subCategoryChoices');
        payload.query = get('subArxivQuery').value;
      }
      if (type === 'journal_rss') {
        payload.feed_urls = requireValue(prefix + 'subFeeds', 'RSS 链接');
        payload.journal = get('subJournal').value;
      }
      return payload;
    }
    function clearSubscriptionForm() {
      ['subId','subQuery','subArxivQuery','subJournal','subFeeds','rssHomepage'].forEach(id => document.getElementById(id).value = '');
      document.getElementById('subSchedule').value = 'manual';
      document.getElementById('subType').value = 'paper';
      document.getElementById('subMax').value = 8;
      document.getElementById('subMin').value = 0.55;
      document.getElementById('subDepth').value = 'standard';
      document.getElementById('subMode').value = 'daily_window';
      renderModules('subModules', ['paper_digest']);
      renderArxivCategories('subCategoryChoices', ['cs.AI']);
      renderChannelChoices('subChannelChoices', []);
      document.getElementById('rssResults').innerHTML = '';
      syncSourceFields();
    }
    function subscriptionPayloadFromState(s, overrides = {}) {
      const feed = (s.source?.feeds || [])[0] || {};
      return {
        id: s.id,
        topic_id: s.topic_id,
        type: s.type,
        schedule: s.schedule || 'manual',
        max_papers: s.max_papers || 8,
        min_score: s.min_score ?? 0.55,
        channels: s.channels || [],
        report_modules: s.report_modules || ['paper_digest'],
        enabled: Boolean(s.enabled),
        analysis_depth: s.analysis_depth || 'standard',
        query: s.source?.query || '',
        categories: s.source?.categories || [],
        mode: s.source?.mode || 'daily_window',
        journal: feed.journal || feed.name || '',
        feed_urls: (s.source?.feeds || []).map(item => item.url),
        ...overrides
      };
    }
    async function toggleSubscription(id) {
      const s = state.subscriptions.find(item => item.id === id);
      if (!s) return;
      await postJson('/api/subscription', subscriptionPayloadFromState(s, { enabled: !s.enabled }));
      await loadState();
    }
    function openSubscriptionEdit(id) {
      const s = state.subscriptions.find(item => item.id === id);
      if (!s) return;
      openModal('编辑订阅', `
        <div class="stack">
          <div class="form-grid">
            <label>研究主题 <span class="required">必填</span><select id="editsubTopic">${state.topics.map(t => `<option value="${esc(t.id)}">${esc(t.name)}</option>`).join('')}</select></label>
            <label>订阅类型 <span class="required">必填</span><select id="editsubType" onchange="syncEditSourceFields()"><option value="paper">常规论文精选</option><option value="arxiv">arXiv 新文追踪</option><option value="journal_rss">期刊 RSS 新文追踪</option></select></label>
            <label class="checkbox-line" style="margin-top:25px"><input type="checkbox" id="editsubEnabled">启用订阅</label>
          </div>
          <div id="editpaperFields" class="source-field"><label>补充检索式 <span class="optional">选填</span><input id="editsubQuery" placeholder="literature recommendation ranking"></label></div>
          <div id="editarxivFields" class="source-field hidden form-grid">
            <div><div class="muted">arXiv 分类 <span class="optional">选填</span></div><div id="editsubCategoryChoices" class="check-grid"></div></div>
            <label>arXiv 检索词 <span class="optional">选填</span><input id="editsubArxivQuery"></label>
          </div>
          <div id="editjournalFields" class="source-field hidden form-grid">
            <label>RSS 链接 <span class="required">必填</span><textarea id="editsubFeeds"></textarea></label>
            <label>期刊名 <span class="optional">选填</span><input id="editsubJournal"></label>
          </div>
          <div>
            <div class="muted">报告模块 <span class="required">必选至少一项</span></div>
            <div id="editsubModules" class="check-grid"></div>
          </div>
          <div>
            <div class="muted">推送渠道 <span class="optional">选填</span></div>
            <div id="editsubChannelChoices" class="check-grid"></div>
          </div>
          <details>
            <summary>选填与高级参数</summary>
            <div class="form-grid" style="margin-top:12px">
              <label>运行计划 <span class="optional">选填</span><select id="editsubSchedule"><option value="manual">手动运行</option><option value="daily 07:00">每天 07:00</option><option value="daily 09:00">每天 09:00</option><option value="weekly monday 09:00">每周一 09:00</option></select></label>
              <label>最多论文数 <span class="optional">选填</span><input id="editsubMax" type="number" min="1" max="100"></label>
              <label>最低推荐分 <span class="optional">选填</span><input id="editsubMin" type="number" step="0.05" min="0" max="1"></label>
              <label>分析深度 <span class="optional">选填</span><select id="editsubDepth"><option value="standard">标准分析</option><option value="brief">简短摘要</option><option value="deep">深度分析</option></select></label>
              <label>arXiv 范围 <span class="optional">选填</span><select id="editsubMode"><option value="daily_window">每日新增窗口</option><option value="latest">最近更新</option><option value="category_latest">分类最新文章</option></select></label>
            </div>
          </details>
        </div>`,
        `<button onclick="saveSubscriptionEdit('${esc(s.id)}')">保存修改</button>`
      );
      document.getElementById('editsubTopic').value = s.topic_id;
      document.getElementById('editsubType').value = s.type;
      document.getElementById('editsubEnabled').checked = Boolean(s.enabled);
      document.getElementById('editsubQuery').value = s.source?.query || '';
      document.getElementById('editsubArxivQuery').value = s.source?.query || '';
      document.getElementById('editsubFeeds').value = (s.source?.feeds || []).map(item => item.url).join('\\n');
      document.getElementById('editsubJournal').value = ((s.source?.feeds || [])[0] || {}).journal || '';
      document.getElementById('editsubSchedule').value = s.schedule || 'manual';
      document.getElementById('editsubMax').value = s.max_papers || 8;
      document.getElementById('editsubMin').value = s.min_score ?? 0.55;
      document.getElementById('editsubDepth').value = s.analysis_depth || 'standard';
      document.getElementById('editsubMode').value = s.source?.mode || 'daily_window';
      renderModules('editsubModules', s.report_modules || ['paper_digest']);
      renderArxivCategories('editsubCategoryChoices', s.source?.categories || ['cs.AI']);
      renderChannelChoices('editsubChannelChoices', s.channels || []);
      syncEditSourceFields();
    }
    function syncEditSourceFields() {
      const type = document.getElementById('editsubType').value;
      document.getElementById('editpaperFields').classList.toggle('hidden', type !== 'paper');
      document.getElementById('editarxivFields').classList.toggle('hidden', type !== 'arxiv');
      document.getElementById('editjournalFields').classList.toggle('hidden', type !== 'journal_rss');
    }
    async function saveSubscriptionEdit(id) {
      try {
        const payload = subscriptionPayloadFromForm('edit');
        payload.id = id;
        await postJson('/api/subscription', payload);
        closeModal();
        await loadState();
      } catch (error) { showError(error); }
    }
    async function deleteSubscription(id) {
      if (!confirm(`删除订阅 ${id}？`)) return;
      await deleteJson(`/api/subscription?id=${encodeURIComponent(id)}`);
      await loadState();
    }
    async function discoverRss() {
      try {
        const data = await postJson('/api/rss-discover', { journal: document.getElementById('subJournal').value, homepage_url: document.getElementById('rssHomepage').value });
        document.getElementById('rssResults').innerHTML = (data.candidates || []).map(c => `
          <div class="card">
            <strong>${esc(c.title || c.url || 'RSS candidate')}</strong>
            <div class="muted">${esc(c.confidence)} · ${esc(c.reason)}</div>
            <div class="hint">${esc(c.url)}</div>
            <button class="secondary" onclick="useRss('${esc(c.url)}')">使用这个 RSS</button>
          </div>`).join('') || '<div class="hint">没有发现 RSS，请手动粘贴 feed URL。</div>';
      } catch (error) { showError(error); }
    }
    function useRss(url) {
      document.getElementById('subFeeds').value = url;
    }

    function renderNotifications() {
      const channels = state.notifications || {};
      document.getElementById('notificationsList').innerHTML = channelKeys().map(channel => {
        const cfg = channels[channel] || {};
        const doc = channelDocs[channel] || [];
        const docLink = doc[1]
          ? `<a class="doc-link" href="${esc(doc[1])}" target="_blank" rel="noopener noreferrer">${esc(doc[0])}</a>`
          : `<span class="hint">${esc(doc[0] || '请按接收端官方文档配置。')}</span>`;
        const fields = channelFields[channel].map(([key, label, type, requirement]) => {
          const value = cfg[key] ?? '';
          const badge = requirement === 'enabled_required'
            ? '<span class="required">启用时必填</span>'
            : '<span class="optional">选填</span>';
          const labelText = `${esc(label)} ${badge}`;
          if (type === 'textarea') {
            return `<label>${labelText}<textarea data-channel="${channel}" data-key="${key}">${esc(value)}</textarea></label>`;
          }
          if (type === 'select') {
            const options = (channelSelectOptions[key] || []).map(([optionValue, optionLabel]) =>
              `<option value="${esc(optionValue)}" ${String(value || 'markdown') === optionValue ? 'selected' : ''}>${esc(optionLabel)}</option>`
            ).join('');
            return `<label>${labelText}<select data-channel="${channel}" data-key="${key}">${options}</select></label>`;
          }
          return `<label>${labelText}<input type="${type}" data-channel="${channel}" data-key="${key}" value="${esc(value)}"></label>`;
        }).join('');
        return `<div class="card channel-card">
          <div class="between"><strong>${esc(channelLabels[channel] || channel)}</strong><span class="pill ${cfg.enabled ? 'ok' : 'off'}">${cfg.enabled ? '已启用' : '未启用'}</span></div>
          <div>${docLink}</div>
          <label class="checkbox-line"><input type="checkbox" data-channel="${channel}" data-key="enabled" ${cfg.enabled ? 'checked' : ''}>启用</label>
          <div class="form-grid">${fields}</div>
          <div class="row">
            <button onclick="saveChannel('${channel}')">保存</button>
            <button class="secondary" onclick="testChannel('${channel}')">测试</button>
            <button class="danger" onclick="resetChannel('${channel}')">重置</button>
          </div>
        </div>`;
      }).join('');
    }
    function collectChannel(channel) {
      const payload = { channel, config: {} };
      document.querySelectorAll(`[data-channel="${channel}"]`).forEach(input => {
        const key = input.dataset.key;
        payload.config[key] = input.type === 'checkbox' ? input.checked : input.value;
      });
      return payload;
    }
    function validateChannelPayload(channel, payload) {
      if (!payload.config.enabled) return;
      const missing = channelFields[channel]
        .filter(([key, _label, _type, requirement]) => requirement === 'enabled_required' && !String(payload.config[key] ?? '').trim())
        .map(([_key, label]) => label);
      if (missing.length) {
        throw new Error(`${channelLabels[channel] || channel}：${missing.join('、')} 启用时必填`);
      }
    }
    async function saveChannel(channel) {
      try {
        const payload = collectChannel(channel);
        validateChannelPayload(channel, payload);
        await postJson('/api/notification', payload);
        await loadState();
      } catch (error) { showError(error); }
    }
    async function testChannel(channel) {
      const data = await postJson(`/api/test-notification?channel=${encodeURIComponent(channel)}`, {});
      document.getElementById('output').textContent = JSON.stringify(data, null, 2);
      showReportsTab();
    }
    async function resetChannel(channel) {
      if (!confirm(`重置 ${channelLabels[channel] || channel}？`)) return;
      await deleteJson(`/api/notification?channel=${encodeURIComponent(channel)}`);
      await loadState();
    }

    function renderSettings() {
      const rt = state.runtime || {};
      const llm = rt.llm || {};
      document.getElementById('llmEnabled').checked = Boolean(llm.enabled);
      document.getElementById('llmBaseUrl').value = llm.base_url || '';
      document.getElementById('llmModel').value = llm.model || '';
      document.getElementById('llmApiType').value = llm.api_type || 'openai_chat';
      document.getElementById('llmApiKey').placeholder = llm.api_key_set ? '已配置，留空不修改' : '未配置';
      const embedding = rt.embedding || {};
      document.getElementById('embeddingEnabled').checked = Boolean(embedding.enabled);
      document.getElementById('embeddingMode').value = rt.ranking?.library_similarity?.mode || 'lexical';
      document.getElementById('embeddingBaseUrl').value = embedding.base_url || '';
      document.getElementById('embeddingModel').value = embedding.model || '';
      document.getElementById('embeddingBatch').value = embedding.batch_size || 64;
      document.getElementById('embeddingApiKey').placeholder = embedding.api_key_set ? '已配置，留空不修改' : '未配置';
      const zotero = rt.zotero || {};
      document.getElementById('zoteroEnabled').checked = Boolean(zotero.enabled);
      document.getElementById('zoteroUserId').value = zotero.user_id || '';
      document.getElementById('zoteroGroupId').value = zotero.group_id || '';
      document.getElementById('zoteroApiKey').placeholder = zotero.api_key_set ? '已配置，留空不修改' : '未配置';
      document.getElementById('zoteroCollections').value = csv(zotero.collections);
      document.getElementById('zoteroTags').value = csv(zotero.tags);
      document.getElementById('zoteroInclude').value = csv(zotero.include_path);
      document.getElementById('zoteroIgnore').value = csv(zotero.ignore_path);
      const sources = rt.sources || {};
      document.getElementById('openalexEnabled').checked = sources.openalex?.enabled !== false;
      document.getElementById('openalexApiKey').placeholder = sources.openalex?.api_key_set ? '已配置，留空不修改' : '未配置';
      document.getElementById('crossrefEnabled').checked = sources.crossref?.enabled !== false;
      document.getElementById('crossrefEmail').value = sources.crossref?.email || '';
      document.getElementById('arxivEnabled').checked = sources.arxiv?.enabled !== false;
      document.getElementById('journalRssEnabled').checked = sources.journal_rss?.enabled !== false;
    }
    async function saveSettings() {
      try {
        await postJson('/api/settings', {
          llm: {
            enabled: document.getElementById('llmEnabled').checked,
            api_key: document.getElementById('llmApiKey').value,
            base_url: document.getElementById('llmBaseUrl').value,
            model: document.getElementById('llmModel').value,
            api_type: document.getElementById('llmApiType').value
          },
          embedding: {
            enabled: document.getElementById('embeddingEnabled').checked,
            mode: document.getElementById('embeddingMode').value,
            api_key: document.getElementById('embeddingApiKey').value,
            base_url: document.getElementById('embeddingBaseUrl').value,
            model: document.getElementById('embeddingModel').value,
            batch_size: document.getElementById('embeddingBatch').value
          },
          zotero: {
            enabled: document.getElementById('zoteroEnabled').checked,
            user_id: document.getElementById('zoteroUserId').value,
            group_id: document.getElementById('zoteroGroupId').value,
            api_key: document.getElementById('zoteroApiKey').value,
            collections: document.getElementById('zoteroCollections').value,
            tags: document.getElementById('zoteroTags').value,
            include_path: document.getElementById('zoteroInclude').value,
            ignore_path: document.getElementById('zoteroIgnore').value
          },
          sources: {
            openalex: { enabled: document.getElementById('openalexEnabled').checked, api_key: document.getElementById('openalexApiKey').value },
            crossref: { enabled: document.getElementById('crossrefEnabled').checked, email: document.getElementById('crossrefEmail').value },
            arxiv: { enabled: document.getElementById('arxivEnabled').checked },
            journal_rss: { enabled: document.getElementById('journalRssEnabled').checked }
          }
        });
        document.getElementById('output').textContent = '设置已保存。';
        await loadState();
      } catch (error) { showError(error); }
    }

    function renderReports() {
      document.getElementById('reportsList').innerHTML = (state.reports || []).map(r => `
        <div class="card">
          <strong>${esc(r.id)}</strong>
          <div class="muted">${esc(r.created_at)} · ${esc(r.subscription_id)}</div>
          <div>candidates ${esc(r.candidate_count)} · recommendations ${esc(r.recommendation_count)}</div>
          <button class="secondary" onclick="showReport('${esc(r.id)}')">查看</button>
        </div>`).join('');
      if (state.reports && state.reports[0] && document.getElementById('output').textContent === 'Ready.') {
        document.getElementById('output').textContent = state.reports[0].report_markdown;
      }
    }
    function showReport(id) {
      const report = state.reports.find(item => item.id === id);
      if (report) document.getElementById('output').textContent = report.report_markdown;
    }
    function showReportsTab() {
      document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
      document.getElementById('view-reports').classList.add('active');
      document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      document.querySelectorAll('.tab')[4].classList.add('active');
    }
    async function runSub(id) {
      showReportsTab();
      document.getElementById('output').textContent = 'Running...';
      const res = await fetch(`/api/run?subscription=${encodeURIComponent(id)}&no_push=true`, { method:'POST' });
      const data = await res.json();
      document.getElementById('output').textContent = data.report || JSON.stringify(data, null, 2);
      await loadState();
    }
    async function runFirst() {
      const sub = state.subscriptions.find(item => item.enabled) || state.subscriptions[0];
      if (sub) await runSub(sub.id);
    }
    function openModal(title, body, actions) {
      document.getElementById('modalTitle').textContent = title;
      document.getElementById('modalBody').innerHTML = body;
      document.getElementById('modalActions').innerHTML = actions;
      document.getElementById('modalBackdrop').classList.remove('hidden');
    }
    function closeModal() {
      document.getElementById('modalBackdrop').classList.add('hidden');
    }
    async function postJson(url, payload) {
      const res = await fetch(url, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || 'request failed');
      return data;
    }
    async function deleteJson(url) {
      const res = await fetch(url, { method:'DELETE' });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || 'delete failed');
      return data;
    }
    loadState();
  </script>
</body>
</html>"""
