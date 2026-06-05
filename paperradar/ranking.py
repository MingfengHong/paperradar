from __future__ import annotations

import math
from collections import Counter
from datetime import datetime, timezone
from typing import Any

import requests

from .models import LibraryItem, Paper, Subscription, Topic


STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "this",
    "that",
    "paper",
    "study",
    "using",
    "based",
    "research",
    "method",
    "model",
}


def library_similarity_rerank(
    papers: list[Paper],
    library_items: list[LibraryItem],
    ranking_config: dict[str, Any] | None = None,
) -> dict[str, tuple[float, list[str]]]:
    if not papers or not library_items:
        return {}
    config = ranking_config or {}
    sim_cfg = config.get("library_similarity", {})
    min_related = float(sim_cfg.get("min_related_score", 0.18))
    corpus = sorted(library_items, key=library_sort_key, reverse=True)
    candidate_texts = [paper_text(paper) for paper in papers]
    corpus_texts = [library_text(item) for item in corpus]
    matrix = similarity_matrix(candidate_texts, corpus_texts, config)
    weights = time_decay_weights(len(corpus)) if sim_cfg.get("time_decay", True) else equal_weights(len(corpus))
    result: dict[str, tuple[float, list[str]]] = {}
    for paper, row in zip(papers, matrix):
        weighted_score = sum(score * weight for score, weight in zip(row, weights))
        best = sorted(
            ((score, item.title) for score, item in zip(row, corpus) if score >= min_related),
            reverse=True,
            key=lambda pair: pair[0],
        )
        related = [title for _, title in best[:3]]
        if not related and row:
            max_score = max(row)
            if max_score > 0:
                related = [corpus[row.index(max_score)].title]
        result[paper.normalized_key()] = (min(1.0, weighted_score * 4), related)
    return result


def prefilter_for_llm(
    topic: Topic,
    subscription: Subscription,
    papers: list[Paper],
    library_items: list[LibraryItem],
    ranking_config: dict[str, Any] | None = None,
) -> tuple[list[Paper], dict[str, tuple[float, list[str]]]]:
    config = ranking_config or {}
    if not papers:
        return [], {}
    deduped: dict[str, Paper] = {}
    for paper in papers:
        if paper.title:
            deduped.setdefault(paper.normalized_key(), paper)
    candidates = list(deduped.values())
    sim_cfg = config.get("library_similarity", {})
    library_signals = library_similarity_rerank(candidates, library_items, config) if sim_cfg.get("enabled", True) else {}
    limit = int(config.get("llm_candidate_limit") or max(subscription.max_papers * 4, 20))
    limit = max(subscription.max_papers, min(limit, len(candidates)))
    topic_vector = Counter(tokenize(topic_text(topic)))
    scored = []
    for paper in candidates:
        paper_vector = Counter(tokenize(paper_text(paper)))
        topic_score = cosine_counters(topic_vector, paper_vector)
        keyword_score = keyword_overlap(topic, paper)
        library_score = library_signals.get(paper.normalized_key(), (0.0, []))[0]
        freshness = freshness_score(paper)
        metadata = 1.0 if paper.abstract else 0.45
        score = 0.42 * max(topic_score, keyword_score) + 0.30 * library_score + 0.16 * freshness + 0.12 * metadata
        scored.append((score, paper))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [paper for _, paper in scored[:limit]], library_signals


def similarity_matrix(left: list[str], right: list[str], config: dict[str, Any]) -> list[list[float]]:
    embedding_cfg = config.get("embedding", {})
    mode = str(config.get("library_similarity", {}).get("mode") or "lexical")
    if mode == "embedding_api" and embedding_cfg.get("enabled") and embedding_cfg.get("api_key"):
        try:
            return embedding_similarity_matrix(left, right, embedding_cfg)
        except Exception:
            return lexical_similarity_matrix(left, right)
    return lexical_similarity_matrix(left, right)


def embedding_similarity_matrix(left: list[str], right: list[str], config: dict[str, Any]) -> list[list[float]]:
    texts = [truncate_text(text) for text in left + right]
    vectors = embedding_vectors(texts, config)
    left_vectors = vectors[: len(left)]
    right_vectors = vectors[len(left) :]
    return [[cosine_vectors(a, b) for b in right_vectors] for a in left_vectors]


def embedding_vectors(texts: list[str], config: dict[str, Any]) -> list[list[float]]:
    base_url = str(config.get("base_url") or "https://api.openai.com/v1").rstrip("/")
    api_key = str(config.get("api_key") or "")
    model = str(config.get("model") or "text-embedding-3-small")
    batch_size = int(config.get("batch_size") or 64)
    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        response = requests.post(
            f"{base_url}/embeddings",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model, "input": batch},
            timeout=60,
        )
        response.raise_for_status()
        data = response.json().get("data", [])
        vectors.extend([list(item.get("embedding") or []) for item in data])
    if len(vectors) != len(texts):
        raise ValueError("embedding response size mismatch")
    return vectors


def lexical_similarity_matrix(left: list[str], right: list[str]) -> list[list[float]]:
    left_vectors = [Counter(tokenize(text)) for text in left]
    right_vectors = [Counter(tokenize(text)) for text in right]
    return [[cosine_counters(a, b) for b in right_vectors] for a in left_vectors]


def tokenize(value: str) -> list[str]:
    import re

    return [token for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", value.lower()) if token not in STOPWORDS]


def cosine_counters(left: Counter[str], right: Counter[str]) -> float:
    if not left or not right:
        return 0.0
    overlap = sum(min(left[key], right[key]) for key in left.keys() & right.keys())
    denom = math.sqrt(sum(v * v for v in left.values())) * math.sqrt(sum(v * v for v in right.values()))
    return min(1.0, overlap / denom) if denom else 0.0


def cosine_vectors(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    return max(0.0, min(1.0, dot / (left_norm * right_norm))) if left_norm and right_norm else 0.0


def time_decay_weights(length: int) -> list[float]:
    raw = [1 / (1 + math.log10(index + 1)) for index in range(length)]
    total = sum(raw) or 1.0
    return [value / total for value in raw]


def equal_weights(length: int) -> list[float]:
    if length <= 0:
        return []
    return [1 / length] * length


def paper_text(paper: Paper) -> str:
    return " ".join([paper.title, paper.abstract, paper.venue, " ".join(paper.authors), " ".join(map(str, paper.extra.values()))])


def topic_text(topic: Topic) -> str:
    return " ".join([topic.name, topic.research_question, " ".join(topic.keywords), " ".join(topic.exclude_keywords), " ".join(topic.venues), topic.reading_goal])


def keyword_overlap(topic: Topic, paper: Paper) -> float:
    if not topic.keywords:
        return 0.0
    text = paper_text(paper).lower()
    hits = sum(1 for keyword in topic.keywords if keyword.lower() in text)
    exclude_hits = sum(1 for keyword in topic.exclude_keywords if keyword.lower() in text)
    return max(0.0, min(1.0, hits / max(1, min(len(topic.keywords), 6)) - 0.25 * exclude_hits))


def freshness_score(paper: Paper) -> float:
    if paper.published_at or paper.updated_at:
        return 1.0
    if paper.year and paper.year >= 2025:
        return 1.0
    if paper.year and paper.year >= 2022:
        return 0.75
    if paper.year and paper.year >= 2018:
        return 0.5
    return 0.25


def library_text(item: LibraryItem) -> str:
    return " ".join([item.title, item.venue, " ".join(item.authors), " ".join(item.tags), item.collection, " ".join(item.collection_paths), item.note])


def library_sort_key(item: LibraryItem) -> datetime:
    if item.added_at:
        try:
            value = item.added_at.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(value)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except Exception:
            pass
    return datetime.min.replace(tzinfo=timezone.utc)


def truncate_text(text: str, limit: int = 6000) -> str:
    return text[:limit]
