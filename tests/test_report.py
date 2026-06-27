from paperradar.models import Paper, Recommendation, RunResult, Subscription, Topic
from paperradar.reports import attach_public_report_link, generate_report, public_report_url, save_report


def test_report_contains_selected_modules() -> None:
    topic = Topic(id="t", name="Topic")
    sub = Subscription(id="s", topic_id="t", type="paper", report_modules=["paper_digest", "periodic_review"])
    rec = Recommendation(
        paper=Paper(id="p", title="A useful paper"),
        worth_read_score=0.9,
        relevance_score=0.9,
        novelty_score=0.7,
        utility_score=0.8,
        urgency_score=0.5,
        confidence_score=0.8,
        reading_action="精读",
        reason="relevant",
    )
    md, html = generate_report(topic, sub, [rec])
    assert "论文精选" in md
    assert "周期综述" in md
    assert "A useful paper" in html


def test_save_report_builds_pages_site(tmp_path) -> None:
    topic = Topic(id="t", name="Topic")
    sub = Subscription(id="s", topic_id="t", type="paper")
    report_url = public_report_url("https://example.github.io/paperradar/", "run-1")
    md, html = attach_public_report_link("report body\n", "<main>report body</main>", report_url)
    result = RunResult(
        run_id="run-1",
        topic=topic,
        subscription=sub,
        candidates=[],
        recommendations=[],
        report_markdown=md,
        report_html=html,
    )

    save_report(result, tmp_path)

    assert (tmp_path / "site" / "index.html").exists()
    assert (tmp_path / "site" / "configurator.html").exists()
    assert (tmp_path / "site" / "reports" / "run-1.html").exists()
    assert "https://example.github.io/paperradar/reports/run-1.html" in (tmp_path / "site" / "reports" / "run-1.html").read_text(encoding="utf-8")
    site_index = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert "reports/run-1.html" in site_index
    assert "configurator.html" in site_index
