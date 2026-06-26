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
            if channel == "feishu":
                send_feishu(cfg, title, markdown)
            elif channel == "dingtalk":
                send_dingtalk(cfg, title, markdown)
            elif channel == "wework":
                send_wework(cfg, title, markdown)
            elif channel == "email":
                send_email(cfg, title, markdown, html)
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
            results.append(NotificationResult(channel, True, "sent"))
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


def send_email(cfg: dict[str, Any], title: str, markdown: str, html: str) -> None:
    sender = cfg.get("from")
    password = cfg.get("password")
    recipients = [part.strip() for part in str(cfg.get("to") or "").replace(";", ",").split(",") if part.strip()]
    if not sender or not password or not recipients:
        raise RuntimeError("email sender/password/to missing")
    server = cfg.get("smtp_server") or infer_smtp(sender)
    port = int(cfg.get("smtp_port") or 587)
    email_markdown = build_email_markdown(markdown)
    email_html = build_email_html(email_markdown)
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


def build_email_markdown(markdown: str, max_lines: int = 80, max_line_length: int = 220) -> str:
    lines = [
        "PaperRadar paper recommendation digest",
        "",
        "A concise email summary is shown below. Open the generated PaperRadar report for detailed analysis.",
        "",
    ]
    url_pattern = re.compile(r"https?://\S+", re.IGNORECASE)
    skipped_prefixes = ("DOI", "DOI：", "arXiv", "arXiv：", "链接", "链接：", "Full report")
    kept_prefixes = ("# ", "## ", "### ", "- **")
    kept_terms = ("订阅：", "生成时间：", "报告模块：", "建议：", "本次没有", "过滤论文数量")
    blank_pending = False
    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            blank_pending = bool(lines and lines[-1])
            continue
        normalized = stripped.lstrip("-").strip()
        if url_pattern.search(stripped) or normalized.startswith(skipped_prefixes):
            continue
        if not stripped.startswith(kept_prefixes) and not any(term in stripped for term in kept_terms):
            continue
        if len(line) > max_line_length:
            line = line[: max_line_length - 3].rstrip() + "..."
        if blank_pending and lines[-1]:
            lines.append("")
        lines.append(line)
        blank_pending = False
        if len(lines) >= max_lines:
            lines.extend(["", "More items were omitted from this email summary. Please open the full PaperRadar report."])
            break
    return "\n".join(lines).strip() + "\n"


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
