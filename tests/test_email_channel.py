from paperradar.notifications.channels import build_email_markdown


def test_email_markdown_removes_link_dense_lines() -> None:
    source = "\n".join(
        [
            "# PaperRadar report",
            "",
            "- **A useful paper**",
            "  - DOI：10.0000/example",
            "  - arXiv：2501.00001",
            "  - 链接：https://example.test/paper",
            "  - 理由：" + "valuable " * 80,
        ]
    )

    result = build_email_markdown(source)

    assert "https://example.test" not in result
    assert "10.0000/example" not in result
    assert "2501.00001" not in result
    assert "A useful paper" in result
    assert "..." in result
