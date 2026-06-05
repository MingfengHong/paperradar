import json
from pathlib import Path

from paperradar.config import init_project
from paperradar.llm import LLMClient
from paperradar.models import Paper, Subscription, Topic
from paperradar.runner import Runner


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
