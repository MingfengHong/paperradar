from pathlib import Path

import pytest

from paperradar.config import AppConfig, init_project, read_yaml
from paperradar.web.server import (
    delete_subscription,
    delete_topic,
    reset_notification,
    runtime_state,
    upsert_runtime_settings,
    upsert_notification,
    upsert_subscription,
    upsert_topic,
)


def test_web_upsert_topic_and_subscription(tmp_path: Path) -> None:
    init_project(tmp_path)
    topic_id = upsert_topic(
        tmp_path,
        {
            "name": "AI for Science",
            "research_question": "What papers are worth reading?",
            "keywords": "ai, science",
        },
    )
    sub_id = upsert_subscription(
        tmp_path,
        {
            "topic_id": topic_id,
            "type": "arxiv",
            "categories": "cs.AI,cs.CL",
            "report_modules": ["fresh_updates"],
            "max_papers": 5,
        },
    )
    config = AppConfig(tmp_path)
    assert config.topic_by_id(topic_id)
    subscription = next(sub for sub in config.subscriptions() if sub.id == sub_id)
    assert subscription.type == "arxiv"
    assert subscription.source["categories"] == ["cs.AI", "cs.CL"]


def test_web_delete_topic_cascades_subscriptions(tmp_path: Path) -> None:
    init_project(tmp_path)
    topic_id = upsert_topic(tmp_path, {"id": "delete-me", "name": "Delete Me", "keywords": "llm"})
    sub_id = upsert_subscription(
        tmp_path,
        {"topic_id": topic_id, "type": "paper", "query": "llm recommendation", "report_modules": ["paper_digest"]},
    )

    with pytest.raises(ValueError, match="topic has subscriptions"):
        delete_topic(tmp_path, topic_id)

    delete_topic(tmp_path, topic_id, cascade=True)
    config = AppConfig(tmp_path)
    assert config.topic_by_id(topic_id) is None
    assert sub_id not in {subscription.id for subscription in config.subscriptions()}


def test_web_delete_subscription(tmp_path: Path) -> None:
    init_project(tmp_path)
    topic_id = upsert_topic(tmp_path, {"id": "subscription-topic", "name": "Subscription Topic"})
    sub_id = upsert_subscription(
        tmp_path,
        {"topic_id": topic_id, "type": "paper", "query": "paper ranking", "report_modules": ["paper_digest"]},
    )

    delete_subscription(tmp_path, sub_id)
    assert sub_id not in {subscription.id for subscription in AppConfig(tmp_path).subscriptions()}

    with pytest.raises(ValueError, match="subscription not found"):
        delete_subscription(tmp_path, sub_id)


def test_web_paper_subscription_allows_empty_query(tmp_path: Path) -> None:
    init_project(tmp_path)
    topic_id = upsert_topic(
        tmp_path,
        {
            "id": "topic-query-fallback",
            "name": "Topic Query Fallback",
            "research_question": "How should AI systems recommend papers?",
            "keywords": "literature recommendation, paper ranking",
        },
    )

    sub_id = upsert_subscription(
        tmp_path,
        {"topic_id": topic_id, "type": "paper", "query": "", "report_modules": ["paper_digest"]},
    )

    subscription = next(sub for sub in AppConfig(tmp_path).subscriptions() if sub.id == sub_id)
    assert subscription.source["query"] == ""


def test_web_upsert_subscription_rejects_missing_topic(tmp_path: Path) -> None:
    init_project(tmp_path)

    with pytest.raises(ValueError, match="topic not found"):
        upsert_subscription(
            tmp_path,
            {"topic_id": "missing-topic", "type": "paper", "query": "paper ranking", "report_modules": ["paper_digest"]},
        )


def test_web_upsert_and_reset_notification(tmp_path: Path) -> None:
    init_project(tmp_path)

    channel = upsert_notification(
        tmp_path,
        {
            "channel": "email",
            "config": {
                "enabled": "true",
                "from": "sender@example.com",
                "to": "reader@example.com",
                "smtp_server": "smtp.example.com",
                "smtp_port": "2525",
                "ignored_field": "not persisted",
            },
        },
    )
    assert channel == "email"
    data = read_yaml(tmp_path / "config" / "notifications.yaml")
    email = data["channels"]["email"]
    assert email["enabled"] is True
    assert email["smtp_port"] == 2525
    assert "ignored_field" not in email

    reset_notification(tmp_path, "email")
    data = read_yaml(tmp_path / "config" / "notifications.yaml")
    assert data["channels"]["email"]["enabled"] is False
    assert data["channels"]["email"]["smtp_port"] == 587


def test_web_runtime_settings_do_not_expose_secrets(tmp_path: Path) -> None:
    init_project(tmp_path)

    upsert_runtime_settings(
        tmp_path,
        {
            "llm": {
                "enabled": True,
                "api_key": "llm-secret",
                "base_url": "https://llm.example/v1",
                "model": "model-a",
                "api_type": "openai_chat",
            },
            "embedding": {
                "enabled": True,
                "api_key": "embedding-secret",
                "base_url": "https://embedding.example/v1",
                "model": "embedding-a",
                "batch_size": "16",
                "mode": "embedding_api",
            },
            "zotero": {
                "enabled": True,
                "user_id": "123",
                "api_key": "zotero-secret",
                "collections": "AI, Papers",
                "tags": "reading, core",
            },
            "sources": {
                "openalex": {"enabled": True, "api_key": "openalex-secret", "email": "legacy@example.com"},
                "crossref": {"enabled": False, "email": ""},
            },
        },
    )

    config = AppConfig(tmp_path)
    settings = read_yaml(tmp_path / "config" / "settings.yaml")
    assert settings["llm"]["api_key"] == "llm-secret"
    assert settings["ranking"]["embedding"]["batch_size"] == 16
    assert settings["ranking"]["library_similarity"]["mode"] == "embedding_api"
    assert settings["sources"]["openalex"]["api_key"] == "openalex-secret"
    assert "email" not in settings["sources"]["openalex"]
    assert settings["sources"]["crossref"]["enabled"] is False
    assert config.zotero["zotero"]["tags"] == ["reading", "core"]

    exposed = runtime_state(config)
    assert exposed["llm"]["api_key_set"] is True
    assert "api_key" not in exposed["llm"]
    assert exposed["embedding"]["api_key_set"] is True
    assert "api_key" not in exposed["embedding"]
    assert exposed["sources"]["openalex"]["api_key_set"] is True
    assert "api_key" not in exposed["sources"]["openalex"]
    assert "email" not in exposed["sources"]["openalex"]
    assert exposed["zotero"]["api_key_set"] is True
    assert "api_key" not in exposed["zotero"]
