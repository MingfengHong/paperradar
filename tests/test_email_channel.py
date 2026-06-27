import smtplib

from paperradar.notifications.channels import build_email_markdown, send_report


EMAIL_CONFIG = {
    "channels": {
        "email": {
            "enabled": True,
            "from": "sender@example.test",
            "password": "password",
            "to": "reader@example.test",
            "smtp_server": "smtp.example.test",
            "smtp_port": 465,
        }
    }
}


def test_email_markdown_keeps_useful_paper_digest_without_link_dense_fields() -> None:
    source = "\n".join(
        [
            "# PaperRadar 报告：Topic",
            "",
            "- 订阅：daily-paper-digest",
            "- 报告模块：论文精选",
            "",
            "- **A useful paper**",
            "  - Smith, 2026 | Journal of Useful Research | openalex",
            "  - 建议：精读；综合分：0.91；置信度：0.84",
            "  - DOI：10.0000/example",
            "  - arXiv：2501.00001",
            "  - 链接：https://example.test/paper",
            "  - 理由：directly relevant to the configured research topic",
            "  - TL;DR：offers a concise method that the user should read first",
            "  - 主要贡献：shows a better paper ranking workflow",
        ]
    )

    result = build_email_markdown(source)

    assert "https://example.test" not in result
    assert "10.0000/example" not in result
    assert "2501.00001" not in result
    assert "A useful paper" in result
    assert "directly relevant to the configured research topic" in result
    assert "offers a concise method" in result
    assert "shows a better paper ranking workflow" in result
    assert "来源：Smith, 2026 | Journal of Useful Research | openalex" in result
    assert "本次推荐：1 篇" in result


def test_email_retries_brief_fallback_on_smtp_spam_rejection(monkeypatch) -> None:
    calls = []

    def fake_deliver(*args) -> None:
        calls.append(args[-2])
        if len(calls) == 1:
            raise smtplib.SMTPResponseException(554, b"Reject by content spam")

    monkeypatch.setattr("paperradar.notifications.channels.deliver_email", fake_deliver)
    source = "\n".join(
        [
            "# PaperRadar 报告：Topic",
            "",
            "- **Paper one**",
            "  - 建议：精读；综合分：0.91；置信度：0.84",
            "  - 理由：first paper reason",
            "  - TL;DR：first paper summary",
            "",
            "- **Paper two**",
            "  - 建议：略读；综合分：0.75；置信度：0.70",
            "  - 理由：second paper reason",
            "  - TL;DR：second paper summary",
        ]
    )

    result = send_report(EMAIL_CONFIG, ["email"], "PaperRadar: Topic", source, "")[0]

    assert result.ok
    assert result.message == "sent with brief fallback after SMTP spam rejection"
    assert len(calls) == 2
    assert "Paper one" in calls[1]
    assert "Paper two" in calls[1]
    assert "first paper summary" not in calls[1]


def test_email_retries_compact_fallback_when_brief_is_rejected(monkeypatch) -> None:
    calls = []

    def fake_deliver(*args) -> None:
        calls.append(args[-2])
        if len(calls) < 3:
            raise smtplib.SMTPResponseException(554, b"Reject by content spam")

    monkeypatch.setattr("paperradar.notifications.channels.deliver_email", fake_deliver)
    source = "\n".join(
        [
            "# PaperRadar 报告：Topic",
            "",
            "- **Paper one**",
            "  - 建议：精读；综合分：0.91；置信度：0.84",
            "  - 理由：first paper reason",
            "",
            "- **Paper two**",
            "  - 建议：略读；综合分：0.75；置信度：0.70",
            "  - 理由：second paper reason",
        ]
    )

    result = send_report(EMAIL_CONFIG, ["email"], "PaperRadar: Topic", source, "")[0]

    assert result.ok
    assert result.message == "sent with compact fallback after SMTP spam rejection"
    assert len(calls) == 3
    assert "Paper one" in calls[2]
    assert "Paper two" not in calls[2]
