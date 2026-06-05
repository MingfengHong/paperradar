from paperradar.models import LibraryItem, Paper
from paperradar.models import Subscription, Topic
from paperradar.ranking import library_similarity_rerank, prefilter_for_llm, time_decay_weights


def test_time_decay_weights_prioritize_recent_items() -> None:
    weights = time_decay_weights(4)
    assert sum(weights) > 0.99
    assert weights[0] > weights[-1]


def test_library_similarity_returns_related_titles() -> None:
    paper = Paper(id="p", title="LLM based literature recommendation", abstract="A recommender for scientific papers.")
    library = [
        LibraryItem(id="l1", title="Scientific literature recommendation with LLM", note="paper recommender", added_at="2026-01-01T00:00:00Z"),
        LibraryItem(id="l2", title="Unrelated protein folding", note="biology", added_at="2020-01-01T00:00:00Z"),
    ]
    scores = library_similarity_rerank([paper], library, {"library_similarity": {"mode": "lexical", "time_decay": True}})
    score, related = scores[paper.normalized_key()]
    assert score > 0
    assert related[0] == "Scientific literature recommendation with LLM"


def test_prefilter_limits_candidates_before_llm() -> None:
    topic = Topic(id="t", name="LLM recommendation", keywords=["LLM", "recommendation"])
    sub = Subscription(id="s", topic_id="t", type="paper", max_papers=2)
    papers = [
        Paper(id="p1", title="LLM literature recommendation", abstract="Recommend scientific papers with LLMs."),
        Paper(id="p2", title="Language model retrieval", abstract="Retrieval for research papers."),
        Paper(id="p3", title="Protein folding assay", abstract="Biology protocol."),
        Paper(id="p4", title="Battery chemistry", abstract="Materials paper."),
    ]
    selected, _ = prefilter_for_llm(topic, sub, papers, [], {"llm_candidate_limit": 2})
    assert len(selected) == 2
    assert selected[0].id == "p1"
