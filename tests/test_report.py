from paperradar.models import Paper, Recommendation, Subscription, Topic
from paperradar.reports import generate_report


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
