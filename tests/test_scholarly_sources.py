from paperradar.sources.scholarly import fetch_crossref, fetch_openalex


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
    assert "title_and_abstract.search:paper+radar" in captured["url"]
    assert "api_key=openalex-secret" in captured["url"]
    assert "mailto=" not in captured["url"]
    assert captured["timeout"] == 20


def test_fetch_openalex_allows_null_primary_location(monkeypatch) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {
                "results": [
                    {
                        "id": "https://openalex.org/W2",
                        "display_name": "OpenAlex paper without primary location",
                        "publication_year": 2026,
                        "primary_location": None,
                        "authorships": [],
                    }
                ]
            }

    monkeypatch.setattr("paperradar.sources.scholarly.requests.get", lambda url, timeout: FakeResponse())

    papers = fetch_openalex("paper radar", max_results=3)

    assert len(papers) == 1
    assert papers[0].source == "openalex"
    assert papers[0].title == "OpenAlex paper without primary location"


def test_fetch_crossref_filters_peer_review_artifacts(monkeypatch) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {
                "message": {
                    "items": [
                        {"title": ['Review for "A useful paper"'], "type": "peer-review", "DOI": "10.1/review"},
                        {"title": ['Decision letter for "A useful paper"'], "type": "peer-review", "DOI": "10.1/decision"},
                        {"title": ["A useful paper"], "type": "journal-article", "DOI": "10.1/paper"},
                    ]
                }
            }

    monkeypatch.setattr("paperradar.sources.scholarly.requests.get", lambda url, headers, timeout: FakeResponse())

    papers = fetch_crossref("useful paper", max_results=10)

    assert [paper.title for paper in papers] == ["A useful paper"]
