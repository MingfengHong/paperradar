from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from .models import Subscription, Topic


ROOT = Path.cwd()


DEFAULT_SETTINGS = {
    "app": {
        "timezone": "Asia/Shanghai",
        "database": "data/paperradar.db",
        "output_dir": "output",
        "public_base_url": "",
        "web_host": "127.0.0.1",
        "web_port": 8766,
    },
    "llm": {
        "enabled": False,
        "api_type": "openai_chat",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
    },
    "sources": {
        "openalex": {"enabled": True},
        "crossref": {"enabled": True, "email": ""},
        "arxiv": {"enabled": True},
        "journal_rss": {"enabled": True},
    },
    "ranking": {
        "library_similarity": {
            "enabled": True,
            "mode": "lexical",
            "time_decay": True,
            "min_related_score": 0.18,
        },
        "embedding": {
            "enabled": False,
            "base_url": "https://api.openai.com/v1",
            "model": "text-embedding-3-small",
            "batch_size": 64,
        },
        "classifiers": [
            "survey",
            "benchmark",
            "method",
            "system",
            "dataset",
            "application",
            "theory",
            "evaluation",
            "tooling",
            "other",
        ],
        "llm_candidate_limit": 32,
        "llm_analysis": {"enabled": True, "language": "Chinese", "max_papers": 24},
    },
}


DEFAULT_TOPICS = {
    "topics": [
        {
            "id": "default",
            "name": "My Research Topic",
            "research_question": "Personalized literature monitoring for my current research topic",
            "keywords": ["large language model", "scientific literature", "recommendation"],
            "exclude_keywords": [],
            "venues": [],
            "reading_goal": "Find papers worth reading without being overwhelmed",
            "status": "active",
        }
    ]
}


DEFAULT_SUBSCRIPTIONS = {
    "subscriptions": [
        {
            "id": "daily-paper-digest",
            "topic_id": "default",
            "type": "paper",
            "enabled": True,
            "report_modules": ["paper_digest", "fresh_updates"],
            "schedule": "manual",
            "max_papers": 8,
            "min_score": 0.55,
            "channels": [],
            "source": {"query": "large language model scientific literature recommendation"},
        },
        {
            "id": "arxiv-ai",
            "topic_id": "default",
            "type": "arxiv",
            "enabled": False,
            "report_modules": ["fresh_updates"],
            "schedule": "manual",
            "max_papers": 8,
            "min_score": 0.55,
            "channels": [],
            "source": {"categories": ["cs.AI", "cs.CL"], "query": "", "mode": "daily_window"},
        },
        {
            "id": "journal-rss-demo",
            "topic_id": "default",
            "type": "journal_rss",
            "enabled": False,
            "report_modules": ["fresh_updates"],
            "schedule": "manual",
            "max_papers": 8,
            "min_score": 0.55,
            "channels": [],
            "source": {"feeds": []},
        },
    ]
}


DEFAULT_NOTIFICATIONS = {
    "channels": {
        "feishu": {"enabled": False, "webhook_url": ""},
        "email": {"enabled": False, "from": "", "password": "", "to": "", "smtp_server": "", "smtp_port": 587},
        "dingtalk": {"enabled": False, "webhook_url": ""},
        "wework": {"enabled": False, "webhook_url": "", "msg_type": "markdown"},
        "generic": {"enabled": False, "webhook_url": ""},
        "telegram": {"enabled": False, "bot_token": "", "chat_id": ""},
        "ntfy": {"enabled": False, "server_url": "https://ntfy.sh", "topic": "", "token": ""},
        "bark": {"enabled": False, "url": ""},
        "slack": {"enabled": False, "webhook_url": ""},
    }
}


DEFAULT_LIBRARY = {"items": [], "imports": []}
DEFAULT_ZOTERO = {
    "zotero": {
        "enabled": False,
        "user_id": "",
        "group_id": "",
        "api_key": "",
        "collections": [],
        "tags": [],
        "include_path": [],
        "ignore_path": [],
    }
}


class ConfigError(RuntimeError):
    pass


def read_yaml(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        return default or {}
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"Expected mapping in {path}")
    return data


def write_yaml_if_missing(path: Path, data: dict[str, Any]) -> bool:
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, allow_unicode=True, sort_keys=False)
    return True


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class AppConfig:
    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root).resolve()
        load_dotenv(self.root / ".env")
        self.config_dir = self.root / "config"
        self.settings = read_yaml(self.config_dir / "settings.yaml", DEFAULT_SETTINGS)
        self.topics_raw = read_yaml(self.config_dir / "topics.yaml", DEFAULT_TOPICS)
        self.subscriptions_raw = read_yaml(self.config_dir / "subscriptions.yaml", DEFAULT_SUBSCRIPTIONS)
        self.notifications = read_yaml(self.config_dir / "notifications.yaml", DEFAULT_NOTIFICATIONS)
        self.library = read_yaml(self.config_dir / "library.yaml", DEFAULT_LIBRARY)
        self.zotero = read_yaml(self.config_dir / "zotero.yaml", DEFAULT_ZOTERO)
        self._apply_env_overrides()

    def _apply_env_overrides(self) -> None:
        app = self.settings.setdefault("app", {})
        if os.getenv("PAPERRADAR_PUBLIC_BASE_URL"):
            app["public_base_url"] = os.getenv("PAPERRADAR_PUBLIC_BASE_URL")

        llm = self.settings.setdefault("llm", {})
        if os.getenv("LLM_API_KEY"):
            llm["api_key"] = os.getenv("LLM_API_KEY")
            llm["enabled"] = True
        if os.getenv("LLM_BASE_URL"):
            llm["base_url"] = os.getenv("LLM_BASE_URL")
        if os.getenv("LLM_MODEL"):
            llm["model"] = os.getenv("LLM_MODEL")
        if os.getenv("LLM_API_TYPE"):
            llm["api_type"] = os.getenv("LLM_API_TYPE")

        ranking = self.settings.setdefault("ranking", {})
        embedding = ranking.setdefault("embedding", {})
        if os.getenv("EMBEDDING_API_KEY"):
            embedding["api_key"] = os.getenv("EMBEDDING_API_KEY")
            embedding["enabled"] = True
            ranking.setdefault("library_similarity", {})["mode"] = "embedding_api"
        if os.getenv("EMBEDDING_BASE_URL"):
            embedding["base_url"] = os.getenv("EMBEDDING_BASE_URL")
        if os.getenv("EMBEDDING_MODEL"):
            embedding["model"] = os.getenv("EMBEDDING_MODEL")

        sources = self.settings.setdefault("sources", {})
        openalex = sources.setdefault("openalex", {})
        if os.getenv("OPENALEX_API_KEY"):
            openalex["api_key"] = os.getenv("OPENALEX_API_KEY")
            openalex["enabled"] = True

        channels = self.notifications.setdefault("channels", {})
        mapping = {
            "feishu": {"webhook_url": "FEISHU_WEBHOOK_URL"},
            "dingtalk": {"webhook_url": "DINGTALK_WEBHOOK_URL"},
            "wework": {"webhook_url": "WEWORK_WEBHOOK_URL", "msg_type": "WEWORK_MSG_TYPE"},
            "generic": {"webhook_url": "GENERIC_WEBHOOK_URL"},
            "slack": {"webhook_url": "SLACK_WEBHOOK_URL"},
            "bark": {"url": "BARK_URL"},
            "telegram": {"bot_token": "TELEGRAM_BOT_TOKEN", "chat_id": "TELEGRAM_CHAT_ID"},
            "ntfy": {"topic": "NTFY_TOPIC", "server_url": "NTFY_SERVER_URL", "token": "NTFY_TOKEN"},
            "email": {
                "from": "EMAIL_FROM",
                "password": "EMAIL_PASSWORD",
                "to": "EMAIL_TO",
                "smtp_server": "EMAIL_SMTP_SERVER",
                "smtp_port": "EMAIL_SMTP_PORT",
            },
        }
        for channel, envs in mapping.items():
            cfg = channels.setdefault(channel, {})
            changed = False
            for key, env_name in envs.items():
                if os.getenv(env_name):
                    value: Any = os.getenv(env_name)
                    if key == "smtp_port":
                        value = int(str(value))
                    cfg[key] = value
                    changed = True
            if changed:
                cfg["enabled"] = True

        zotero = self.zotero.setdefault("zotero", {})
        if os.getenv("ZOTERO_API_KEY"):
            zotero["api_key"] = os.getenv("ZOTERO_API_KEY")
            zotero["enabled"] = True
        if os.getenv("ZOTERO_USER_ID"):
            zotero["user_id"] = os.getenv("ZOTERO_USER_ID")
            zotero["enabled"] = True
        if os.getenv("ZOTERO_GROUP_ID"):
            zotero["group_id"] = os.getenv("ZOTERO_GROUP_ID")
            zotero["enabled"] = True

    @property
    def database_path(self) -> Path:
        db = self.settings.get("app", {}).get("database", "data/paperradar.db")
        return (self.root / db).resolve()

    @property
    def output_dir(self) -> Path:
        out = self.settings.get("app", {}).get("output_dir", "output")
        return (self.root / out).resolve()

    @property
    def public_base_url(self) -> str:
        return str(self.settings.get("app", {}).get("public_base_url") or "").strip().rstrip("/")

    def topics(self) -> list[Topic]:
        return [Topic.from_dict(item) for item in self.topics_raw.get("topics", [])]

    def subscriptions(self) -> list[Subscription]:
        return [Subscription.from_dict(item) for item in self.subscriptions_raw.get("subscriptions", [])]

    def topic_by_id(self, topic_id: str) -> Topic | None:
        return next((topic for topic in self.topics() if topic.id == topic_id), None)


def init_project(root: Path | str = ".") -> list[Path]:
    root_path = Path(root).resolve()
    created: list[Path] = []
    defaults = {
        "settings.yaml": DEFAULT_SETTINGS,
        "topics.yaml": DEFAULT_TOPICS,
        "subscriptions.yaml": DEFAULT_SUBSCRIPTIONS,
        "notifications.yaml": DEFAULT_NOTIFICATIONS,
        "library.yaml": DEFAULT_LIBRARY,
        "zotero.yaml": DEFAULT_ZOTERO,
    }
    for filename, data in defaults.items():
        path = root_path / "config" / filename
        if write_yaml_if_missing(path, data):
            created.append(path)
    for dirname in ["data", "output/reports", "output/logs", "output/static", "output/site/reports"]:
        (root_path / dirname).mkdir(parents=True, exist_ok=True)
    env_path = root_path / ".env.example"
    if not env_path.exists():
        env_path.write_text(
            "\n".join(
                [
                    "LLM_API_KEY=",
                    "LLM_BASE_URL=https://api.openai.com/v1",
                    "LLM_MODEL=gpt-4o-mini",
                    "PAPERRADAR_PUBLIC_BASE_URL=",
                    "OPENALEX_API_KEY=",
                    "FEISHU_WEBHOOK_URL=",
                    "EMAIL_FROM=",
                    "EMAIL_PASSWORD=",
                    "EMAIL_TO=",
                    "ZOTERO_USER_ID=",
                    "ZOTERO_GROUP_ID=",
                    "ZOTERO_API_KEY=",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        created.append(env_path)
    return created
