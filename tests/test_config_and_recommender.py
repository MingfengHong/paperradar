from pathlib import Path

from paperradar.config import AppConfig, init_project
from paperradar.models import Paper, Subscription, Topic
from paperradar.recommender import recommend


def test_init_project_creates_config(tmp_path: Path) -> None:
    created = init_project(tmp_path)
    assert created
    config = AppConfig(tmp_path)
    assert config.topics()
    assert config.subscriptions()


def test_recommender_scores_relevant_paper() -> None:
    topic = Topic(id="t", name="LLM literature recommendation", keywords=["LLM", "recommendation"])
    sub = Subscription(id="s", topic_id="t", type="paper", min_score=0.1)
    paper = Paper(id="p", title="LLM based scientific literature recommendation", abstract="We recommend papers with large language models.")
    recs = recommend(topic, sub, [paper], [])
    assert recs[0].worth_read_score > 0.1
    assert recs[0].reading_action in {"精读", "略读", "收藏", "观察"}
    assert recs[0].tldr
    assert recs[0].keywords
