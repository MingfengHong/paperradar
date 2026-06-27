from pathlib import Path

from paperradar.config import AppConfig, init_project
from paperradar.cli import apply_quickstart_config, has_ready_notification_channel
from paperradar.models import Paper, Subscription, Topic
from paperradar.recommender import recommend


def test_init_project_creates_config(tmp_path: Path) -> None:
    created = init_project(tmp_path)
    assert created
    config = AppConfig(tmp_path)
    assert config.topics()
    assert config.subscriptions()
    env_example = (tmp_path / ".env.example").read_text(encoding="utf-8")
    assert "EMAIL_SMTP_SERVER=" in env_example
    assert "DINGTALK_WEBHOOK_URL=" in env_example


def test_quickstart_setup_writes_reusable_user_config(tmp_path: Path) -> None:
    init_project(tmp_path)
    apply_quickstart_config(
        tmp_path,
        {
            "topic_name": "Management AI",
            "question": "How does AI change management research?",
            "keywords": "management, artificial intelligence",
            "channel": "email",
            "email_from": "sender@example.com",
            "email_to": "reader@example.com",
            "email_password": "smtp-secret",
            "email_smtp_server": "smtp.example.com",
            "email_smtp_port": "465",
            "feishu_webhook_url": "",
            "llm_api_key": "llm-secret",
            "llm_base_url": "https://llm.example/v1",
            "llm_model": "model-a",
            "openalex_api_key": "openalex-secret",
            "enable_arxiv": True,
            "arxiv_categories": "cs.AI,econ.GN",
        },
    )

    config = AppConfig(tmp_path)
    topic = config.topic_by_id("default")
    assert topic
    assert topic.name == "Management AI"
    subscriptions = {subscription.id: subscription for subscription in config.subscriptions()}
    assert subscriptions["daily-paper-digest"].channels == ["email"]
    assert subscriptions["arxiv-daily"].enabled is True
    assert subscriptions["arxiv-daily"].source["categories"] == ["cs.AI", "econ.GN"]
    assert config.notifications["channels"]["email"]["enabled"] is True
    env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "EMAIL_FROM=sender@example.com" in env
    assert "EMAIL_PASSWORD=smtp-secret" in env
    assert "LLM_API_KEY=llm-secret" in env


def test_ready_notification_requires_channel_credentials() -> None:
    assert not has_ready_notification_channel({"email": {"enabled": True, "from": "a@example.com", "to": "b@example.com"}})
    assert has_ready_notification_channel({"email": {"enabled": True, "from": "a@example.com", "password": "secret", "to": "b@example.com"}})
    assert has_ready_notification_channel({"feishu": {"enabled": True, "webhook_url": "https://example.test/hook"}})


def test_recommender_scores_relevant_paper() -> None:
    topic = Topic(id="t", name="LLM literature recommendation", keywords=["LLM", "recommendation"])
    sub = Subscription(id="s", topic_id="t", type="paper", min_score=0.1)
    paper = Paper(id="p", title="LLM based scientific literature recommendation", abstract="We recommend papers with large language models.")
    recs = recommend(topic, sub, [paper], [])
    assert recs[0].worth_read_score > 0.1
    assert recs[0].reading_action in {"精读", "略读", "收藏", "观察"}
    assert recs[0].tldr
    assert recs[0].keywords
