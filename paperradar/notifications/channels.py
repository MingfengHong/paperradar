from __future__ import annotations

import html as html_lib
import re
import smtplib
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import requests


@dataclass
class NotificationResult:
    channel: str
    ok: bool
    message: str


def send_report(config: dict[str, Any], channels: list[str], title: str, markdown: str, html: str) -> list[NotificationResult]:
    configured = config.get("channels", {})
    targets = channels or [name for name, cfg in configured.items() if cfg.get("enabled")]
    results: list[NotificationResult] = []
    for channel in targets:
        cfg = configured.get(channel, {})
        if not cfg.get("enabled"):
            results.append(NotificationResult(channel, False, "channel disabled or not configured"))
            continue
        try:
            message = "sent"
            if channel == "feishu":
                send_feishu(cfg, title, markdown)
            elif channel == "dingtalk":
                send_dingtalk(cfg, title, markdown)
            elif channel == "wework":
                send_wework(cfg, title, markdown)
            elif channel == "email":
                message = send_email(cfg, title, markdown, html)
            elif channel == "generic":
                send_generic(cfg, title, markdown, html)
            elif channel == "telegram":
                send_telegram(cfg, title, markdown)
            elif channel == "ntfy":
                send_ntfy(cfg, title, markdown)
            elif channel == "bark":
                send_bark(cfg, title, markdown)
            elif channel == "slack":
                send_slack(cfg, title, markdown)
            else:
                results.append(NotificationResult(channel, False, "unsupported channel"))
                continue
            results.append(NotificationResult(channel, True, message))
        except Exception as exc:
            results.append(NotificationResult(channel, False, str(exc)))
    return results


def test_channel(config: dict[str, Any], channel: str) -> NotificationResult:
    result = send_report(config, [channel], "PaperRadar test", "PaperRadar 测试消息", "<p>PaperRadar 测试消息</p>")
    return result[0] if result else NotificationResult(channel, False, "no result")


def split_targets(value: str) -> list[str]:
    return [part.strip() for part in str(value or "").split(";") if part.strip()]


def send_feishu(cfg: dict[str, Any], title: str, markdown: str) -> None:
    for url in split_targets(cfg.get("webhook_url", "")):
        post_json(url, {"msg_type": "interactive", "card": {"header": {"title": {"tag": "plain_text", "content": title}}, "elements": [{"tag": "markdown", "content": truncate(markdown, 28000)}]}})


def send_dingtalk(cfg: dict[str, Any], title: str, markdown: str) -> None:
    for url in split_targets(cfg.get("webhook_url", "")):
        post_json(url, {"msgtype": "markdown", "markdown": {"title": title, "text": truncate(markdown, 18000)}})


def send_wework(cfg: dict[str, Any], title: str, markdown: str) -> None:
    for url in split_targets(cfg.get("webhook_url", "")):
        msg_type = cfg.get("msg_type", "markdown")
        if msg_type == "text":
            post_json(url, {"msgtype": "text", "text": {"content": truncate(f"{title}\n{markdown}", 18000)}})
        else:
            post_json(url, {"msgtype": "markdown", "markdown": {"content": truncate(f"**{title}**\n\n{markdown}", 18000)}})


def send_email(cfg: dict[str, Any], title: str, markdown: str, html: str) -> str:
    sender = cfg.get("from")
    password = cfg.get("password")
    recipients = [part.strip() for part in str(cfg.get("to") or "").replace(";", ",").split(",") if part.strip()]
    if not sender or not password or not recipients:
        raise RuntimeError("email sender/password/to missing")
    server = cfg.get("smtp_server") or infer_smtp(sender)
    port = int(cfg.get("smtp_port") or 587)
    email_markdown = build_email_markdown(markdown)
    email_html = build_email_html(email_markdown)
    try:
        deliver_email(sender, password, recipients, server, port, title, email_markdown, email_html)
        return "sent"
    except smtplib.SMTPResponseException as exc:
        if not is_smtp_spam_rejection(exc):
            raise
        fallback_markdown = build_email_markdown(markdown, max_papers=1, max_line_length=140, max_chars=1800)
        fallback_html = build_email_html(fallback_markdown)
        deliver_email(sender, password, recipients, server, port, title, fallback_markdown, fallback_html)
        return "sent with compact fallback after SMTP spam rejection"


def deliver_email(sender: str, password: str, recipients: list[str], server: str, port: int, title: str, email_markdown: str, email_html: str) -> None:
    message = MIMEMultipart("alternative")
    message["Subject"] = title
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message.attach(MIMEText(email_markdown, "plain", "utf-8"))
    message.attach(MIMEText(email_html, "html", "utf-8"))
    if port == 465:
        with smtplib.SMTP_SSL(server, port, timeout=30) as smtp:
            smtp.login(sender, password)
            smtp.sendmail(sender, recipients, message.as_string())
    else:
        with smtplib.SMTP(server, port, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(sender, password)
            smtp.sendmail(sender, recipients, message.as_string())


def is_smtp_spam_rejection(exc: smtplib.SMTPResponseException) -> bool:
    error = exc.smtp_error.decode("utf-8", errors="ignore") if isinstance(exc.smtp_error, bytes) else str(exc.smtp_error)
    return exc.smtp_code == 554 and "spam" in error.lower()


def build_email_markdown(markdown: str, max_papers: int = 3, max_line_length: int = 120, max_chars: int = 2600) -> str:
    papers = extract_email_papers(markdown)
    lines = [
        "# PaperRadar 论文推送",
        "",
        f"- 本次推荐：{len(papers)} 篇",
    ]
    lines.append("")

    if not papers:
        no_match = first_matching_line(markdown, ("本次没有", "没有达到阈值", "没有高相关"))
        lines.extend(["## 今日结果", no_match or "本次没有达到阈值的推荐论文。", ""])
    else:
        lines.extend(["## 优先阅读清单", ""])
        for index, paper in enumerate(papers[:max_papers], start=1):
            lines.append(f"{index}. {truncate_line(paper['title'], max_line_length)}")
            details = paper["details"]
            assert isinstance(details, list)
            for detail in select_email_details(details):
                lines.append(f"   - {truncate_line(detail, max_line_length)}")
            lines.append("")
        remaining = len(papers) - max_papers
        if remaining > 0:
            lines.extend([f"还有 {remaining} 篇未列出。", ""])

    return truncate_email_body("\n".join(lines).strip() + "\n", max_chars)


def select_email_details(details: list[str], max_details: int = 2) -> list[str]:
    priority_prefixes = ("建议：", "理由：")
    selected: list[str] = []
    for prefix in priority_prefixes:
        match = next((detail for detail in details if detail.startswith(prefix)), "")
        if match:
            selected.append(compact_email_detail(match))
        if len(selected) >= max_details:
            return selected
    for detail in details:
        if detail not in selected:
            selected.append(compact_email_detail(detail))
        if len(selected) >= max_details:
            break
    return selected


def compact_email_detail(detail: str) -> str:
    if detail.startswith("建议："):
        return "；".join(detail.split("；")[:2])
    if detail.startswith("理由："):
        return truncate_line(detail, 68)
    if detail.startswith("TL;DR："):
        return truncate_line(detail, 72)
    return truncate_line(detail, 110)


def extract_email_papers(markdown: str) -> list[dict[str, list[str] | str]]:
    papers: list[dict[str, list[str] | str]] = []
    current: dict[str, list[str] | str] | None = None
    seen_titles: set[str] = set()
    title_pattern = re.compile(r"^- \*\*(.+?)\*\*\s*$")

    def finish_current() -> None:
        if not current:
            return
        title = str(current["title"])
        normalized = " ".join(title.lower().split())
        if normalized and normalized not in seen_titles:
            papers.append(current)
            seen_titles.add(normalized)

    for raw_line in markdown.splitlines():
        stripped = raw_line.strip()
        title_match = title_pattern.match(stripped)
        if title_match:
            finish_current()
            current = {"title": clean_email_text(title_match.group(1)), "details": []}
            continue
        if current is None or not raw_line.startswith("  - "):
            continue
        detail = clean_email_detail(raw_line[4:])
        if detail:
            details = current["details"]
            assert isinstance(details, list)
            details.append(detail)
    finish_current()
    return papers


def clean_email_detail(value: str) -> str:
    text = clean_email_text(value)
    if not text:
        return ""
    skipped_prefixes = ("DOI", "DOI：", "arXiv", "arXiv：", "链接", "链接：", "Full report")
    if text.startswith(skipped_prefixes) or re.search(r"https?://\S+", text, re.IGNORECASE):
        return ""
    return text


def clean_email_text(value: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", value)
    text = re.sub(r"https?://\S+", "", text, flags=re.IGNORECASE)
    text = " ".join(text.split())
    return text.strip(" -")


def first_matching_line(markdown: str, terms: tuple[str, ...]) -> str:
    for raw_line in markdown.splitlines():
        stripped = clean_email_text(raw_line)
        if any(term in stripped for term in terms):
            return stripped
    return ""


def truncate_line(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return value[: max_length - 3].rstrip() + "..."


def truncate_email_body(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 36].rstrip() + "\n\n[邮件摘要已截断，请查看完整报告。]\n"


def build_email_html(markdown: str) -> str:
    body_lines: list[str] = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("# "):
            body_lines.append(f"<h1>{html_lib.escape(stripped[2:])}</h1>")
        elif stripped.startswith("## "):
            body_lines.append(f"<h2>{html_lib.escape(stripped[3:])}</h2>")
        elif stripped.startswith("### "):
            body_lines.append(f"<h3>{html_lib.escape(stripped[4:])}</h3>")
        elif stripped.startswith("- "):
            body_lines.append(f"<p>{html_lib.escape(stripped[2:])}</p>")
        elif stripped.startswith("  - "):
            body_lines.append(f"<p class='detail'>{html_lib.escape(stripped[4:])}</p>")
        else:
            body_lines.append(f"<p>{html_lib.escape(stripped)}</p>")
    return "<!doctype html><html><body style='font-family:Arial,sans-serif;line-height:1.6;color:#222'>" + "".join(body_lines) + "</body></html>"


def send_generic(cfg: dict[str, Any], title: str, markdown: str, html: str) -> None:
    for url in split_targets(cfg.get("webhook_url", "")):
        post_json(url, {"title": title, "markdown": markdown, "html": html})


def send_telegram(cfg: dict[str, Any], title: str, markdown: str) -> None:
    token = cfg.get("bot_token")
    chat_ids = split_targets(cfg.get("chat_id", ""))
    if not token or not chat_ids:
        raise RuntimeError("telegram bot token/chat id missing")
    for chat_id in chat_ids:
        post_json(f"https://api.telegram.org/bot{token}/sendMessage", {"chat_id": chat_id, "text": truncate(f"{title}\n\n{markdown}", 3900)})


def send_ntfy(cfg: dict[str, Any], title: str, markdown: str) -> None:
    server = str(cfg.get("server_url") or "https://ntfy.sh").rstrip("/")
    topic = cfg.get("topic")
    if not topic:
        raise RuntimeError("ntfy topic missing")
    headers = {"Title": title}
    if cfg.get("token"):
        headers["Authorization"] = f"Bearer {cfg['token']}"
    response = requests.post(f"{server}/{topic}", data=truncate(markdown, 12000).encode("utf-8"), headers=headers, timeout=20)
    response.raise_for_status()


def send_bark(cfg: dict[str, Any], title: str, markdown: str) -> None:
    url = cfg.get("url")
    if not url:
        raise RuntimeError("Bark URL missing")
    post_json(url, {"title": title, "body": truncate(markdown, 3500)})


def send_slack(cfg: dict[str, Any], title: str, markdown: str) -> None:
    for url in split_targets(cfg.get("webhook_url", "")):
        post_json(url, {"text": f"*{title}*\n{truncate(markdown, 12000)}"})


def post_json(url: str, payload: dict[str, Any]) -> None:
    if not url:
        raise RuntimeError("webhook URL missing")
    response = requests.post(url, json=payload, timeout=20)
    response.raise_for_status()


def truncate(value: str, limit: int) -> str:
    if len(value.encode("utf-8")) <= limit:
        return value
    result = value
    while len(result.encode("utf-8")) > limit - 40:
        result = result[:-200]
    return result + "\n\n[内容过长，已截断；完整报告请查看 HTML/Markdown 输出。]"


def infer_smtp(sender: str) -> str:
    domain = sender.split("@")[-1].lower()
    if domain == "qq.com":
        return "smtp.qq.com"
    if domain == "163.com":
        return "smtp.163.com"
    if domain == "gmail.com":
        return "smtp.gmail.com"
    if domain in {"outlook.com", "hotmail.com", "live.com"}:
        return "smtp-mail.outlook.com"
    return f"smtp.{domain}"
