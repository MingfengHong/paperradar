import json
from pathlib import Path

import pytest

from paperradar.config import init_project
from paperradar.llm import LLMClient
from paperradar.models import Paper, Subscription, Topic
from paperradar.notifications import NotificationResult
from paperradar.runner import Runner


def clear_notification_env(monkeypatch) -> None:
    for name in [
        "FEISHU_WEBHOOK_URL",
        "DINGTALK_WEBHOOK_URL",
        "WEWORK_WEBHOOK_URL",
        "GENERIC_WEBHOOK_URL",
        "SLACK_WEBHOOK_URL",
        "BARK_URL",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "NTFY_TOPIC",
        "NTFY_TOKEN",
        "EMAIL_FROM",
        "EMAIL_PASSWORD",
        "EMAIL_TO",
        "EMAIL_SMTP_SERVER",
        "EMAIL_SMTP_PORT",
    ]:
        monkeypatch.delenv(name, raising=False)


def test_runner_allows_missing_llm_key(tmp_path: Path, monkeypatch) -> None:
    for name in ["LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "LLM_API_TYPE"]:
        monkeypatch.delenv(name, raising=False)
    init_project(tmp_path)
    monkeypatch.setattr(Runner, "fetch_candidates", lambda self, topic, subscription, llm_client: [Paper(id="p", title="LLM paper recommendation", abstract="A paper recommender.")])
    runner = Runner(tmp_path)
    try:
        result = runner.run_subscription(runner.config.subscriptions()[0], no_push=True)
        assert result.recommendations
    finally:
        runner.close()


def test_runner_requires_notification_channel_when_pushing(tmp_path: Path, monkeypatch) -> None:
    clear_notification_env(monkeypatch)
    init_project(tmp_path)
    monkeypatch.setattr(Runner, "fetch_candidates", lambda self, topic, subscription, llm_client: [Paper(id="p", title="LLM paper recommendation", abstract="A paper recommender.")])
    runner = Runner(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="no notification channels configured"):
            runner.run_subscription(runner.config.subscriptions()[0], no_push=False)
    finally:
        runner.close()


def test_runner_fails_on_notification_error(tmp_path: Path, monkeypatch) -> None:
    init_project(tmp_path)
    monkeypatch.setattr(Runner, "fetch_candidates", lambda self, topic, subscription, llm_client: [Paper(id="p", title="LLM paper recommendation", abstract="A paper recommender.")])
    monkeypatch.setattr("paperradar.runner.send_report", lambda *args: [NotificationResult("email", False, "auth failed")])
    runner = Runner(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="email: auth failed"):
            runner.run_subscription(runner.config.subscriptions()[0], no_push=False)
    finally:
        runner.close()


def test_llm_generates_scholarly_queries(monkeypatch) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {"choices": [{"message": {"content": json.dumps({"queries": ["LLM literature recommendation", "scientific paper recommender"]})}}]}

    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["payload"] = json
        return FakeResponse()

    monkeypatch.setattr("paperradar.llm.requests.post", fake_post)
    client = LLMClient({"enabled": True, "api_key": "test-key", "base_url": "https://example.test/v1", "model": "test-model"})
    topic = Topic(id="t", name="LLM recommendation", keywords=["LLM", "recommendation"])
    sub = Subscription(id="s", topic_id="t", type="paper", source={"query": "seed query"})
    queries = client.plan_scholarly_queries(topic, sub)
    assert queries == ["LLM literature recommendation", "scientific paper recommender"]
    assert captured["payload"]["model"] == "test-model"
