from paperradar.sources.scholarly import fetch_openalex


def test_fetch_openalex_uses_api_key(monkeypatch) -> None:
    captured = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {
                "results": [
                    {
                        "id": "https://openalex.org/W1",
                        "display_name": "PaperRadar source test",
                        "publication_year": 2026,
                        "authorships": [],
                    }
                ]
            }

    def fake_get(url, timeout):
        captured["url"] = url
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("paperradar.sources.scholarly.requests.get", fake_get)

    papers = fetch_openalex("paper radar", max_results=3, api_key="openalex-secret")

    assert papers[0].title == "PaperRadar source test"
    assert "api_key=openalex-secret" in captured["url"]
    assert "mailto=" not in captured["url"]
    assert captured["timeout"] == 20
