from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from .llm import LLMClient
from .models import LibraryItem, Paper, Recommendation, Subscription, Topic
from .ranking import library_similarity_rerank


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
}


def recommend(
    topic: Topic,
    subscription: Subscription,
    papers: list[Paper],
    library_items: list[LibraryItem],
    llm_client: LLMClient | None = None,
    ranking_config: dict[str, Any] | None = None,
    library_signals: dict[str, tuple[float, list[str]]] | None = None,
) -> list[Recommendation]:
    deduped = dedupe(papers)
    ranking_config = ranking_config or {}
    sim_cfg = ranking_config.get("library_similarity", {})
    library_signals = library_signals or (library_similarity_rerank(deduped, library_items, ranking_config) if sim_cfg.get("enabled", True) else {})
    heuristic = [score_paper(topic, subscription, paper, library_items, library_signals.get(paper.normalized_key())) for paper in deduped]
    if llm_client:
        heuristic = llm_client.score_papers(topic, deduped, heuristic)
        heuristic = llm_client.analyze_recommendations(topic, heuristic, ranking_config)
    enrich_without_llm(topic, heuristic, ranking_config)
    ranked = sorted(heuristic, key=lambda rec: rec.total_score(), reverse=True)
    for rec in ranked:
        rec.filtered = rec.total_score() < subscription.min_score or rec.reading_action == "过滤"
    return ranked


def dedupe(papers: list[Paper]) -> list[Paper]:
    result: dict[str, Paper] = {}
    for paper in papers:
        if not paper.title:
            continue
        result.setdefault(paper.normalized_key(), paper)
    return list(result.values())


def score_paper(
    topic: Topic,
    subscription: Subscription,
    paper: Paper,
    library_items: list[LibraryItem],
    library_signal: tuple[float, list[str]] | None = None,
) -> Recommendation:
    text = " ".join([paper.title, paper.abstract, paper.venue, " ".join(paper.authors)]).lower()
    topic_text = " ".join([topic.name, topic.research_question, " ".join(topic.keywords), topic.reading_goal]).lower()
    relevance = similarity(topic_text, text)
    keyword_hits = sum(1 for keyword in topic.keywords if keyword.lower() in text)
    exclude_hits = sum(1 for keyword in topic.exclude_keywords if keyword.lower() in text)
    if topic.keywords:
        relevance = max(relevance, min(1.0, keyword_hits / max(1, min(len(topic.keywords), 6))))
    venue_bonus = 0.1 if any(venue.lower() in paper.venue.lower() for venue in topic.venues if venue) else 0.0
    library_score, related = library_signal or library_relation_score(paper, library_items)
    recency = recency_score(paper)
    metadata_confidence = 0.85 if paper.abstract else 0.55
    worth = max(0.05, min(1.0, 0.55 * relevance + 0.18 * library_score + 0.15 * recency + venue_bonus - 0.18 * exclude_hits))
    utility = max(relevance, library_score)
    novelty = 0.55 + min(0.2, recency / 4)
    urgency = recency if subscription.type in {"arxiv", "journal_rss"} else max(0.25, recency * 0.5)
    action = action_for(worth, metadata_confidence)
    reason = build_reason(topic, paper, relevance, library_score, related, action)
    return Recommendation(
        paper=paper,
        worth_read_score=round(worth, 3),
        relevance_score=round(relevance, 3),
        novelty_score=round(novelty, 3),
        utility_score=round(utility, 3),
        urgency_score=round(urgency, 3),
        confidence_score=round(metadata_confidence, 3),
        reading_action=action,
        reason=reason,
        evidence_level="abstract" if paper.abstract else "metadata",
        related_library_items=related,
        library_similarity_score=round(library_score, 3),
    )


def tokenize(value: str) -> list[str]:
    return [token for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", value.lower()) if token not in STOPWORDS]


def similarity(left: str, right: str) -> float:
    a = Counter(tokenize(left))
    b = Counter(tokenize(right))
    if not a or not b:
        return 0.0
    overlap = sum(min(a[key], b[key]) for key in a.keys() & b.keys())
    denom = math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values()))
    return min(1.0, overlap / denom) if denom else 0.0


def library_relation_score(paper: Paper, library_items: list[LibraryItem]) -> tuple[float, list[str]]:
    if not library_items:
        return 0.0, []
    related: list[tuple[float, str]] = []
    paper_text = " ".join([paper.title, paper.abstract, paper.venue])
    for item in library_items:
        if item.doi and paper.doi and item.doi.lower() == paper.doi.lower():
            related.append((1.0, item.title))
            continue
        score = similarity(paper_text, " ".join([item.title, item.venue, " ".join(item.tags), item.note]))
        if score >= 0.18:
            related.append((score, item.title))
    related.sort(reverse=True, key=lambda pair: pair[0])
    if not related:
        return 0.0, []
    return min(1.0, related[0][0]), [title for _, title in related[:3]]


def recency_score(paper: Paper) -> float:
    if not paper.year:
        return 0.45 if paper.published_at or paper.updated_at else 0.25
    if paper.year >= 2025:
        return 1.0
    if paper.year >= 2022:
        return 0.75
    if paper.year >= 2018:
        return 0.5
    return 0.25


def action_for(worth: float, confidence: float) -> str:
    if confidence < 0.5 and worth >= 0.55:
        return "观察"
    if worth >= 0.78:
        return "精读"
    if worth >= 0.62:
        return "略读"
    if worth >= 0.48:
        return "收藏"
    if worth >= 0.35:
        return "观察"
    return "过滤"


def build_reason(topic: Topic, paper: Paper, relevance: float, library_score: float, related: list[str], action: str) -> str:
    parts: list[str] = []
    if relevance >= 0.55:
        parts.append("与研究主题高度相关")
    elif relevance >= 0.25:
        parts.append("与主题有一定关联")
    else:
        parts.append("主题相关性较弱")
    if library_score >= 0.4 and related:
        parts.append(f"与已有文献《{related[0]}》接近")
    if paper.source == "arxiv":
        parts.append("来自 arXiv 新文追踪")
    if paper.source == "journal_rss":
        parts.append("来自期刊 RSS")
    parts.append(f"建议动作：{action}")
    return "；".join(parts)


def enrich_without_llm(topic: Topic, recommendations: list[Recommendation], ranking_config: dict[str, Any]) -> None:
    classifiers = list(ranking_config.get("classifiers") or [])
    for rec in recommendations:
        if not rec.tldr:
            rec.tldr = fallback_tldr(rec.paper)
        if not rec.keywords:
            rec.keywords = fallback_keywords(topic, rec.paper)
        if not rec.classifier:
            rec.classifier = fallback_classifier(rec.paper, classifiers)
        if not rec.contribution:
            rec.contribution = fallback_contribution(rec.paper)
        if not rec.limitation:
            rec.limitation = "仅基于题名、摘要和元数据判断；完整方法细节需阅读全文确认。"


def fallback_tldr(paper: Paper) -> str:
    text = paper.abstract.strip()
    if not text:
        return "缺少摘要，建议先查看原文元数据和 PDF。"
    sentences = re.split(r"(?<=[.!?。！？])\s+", text)
    return sentences[0][:280]


def fallback_keywords(topic: Topic, paper: Paper) -> list[str]:
    text = " ".join([paper.title, paper.abstract]).lower()
    hits = [keyword for keyword in topic.keywords if keyword.lower() in text]
    tokens = [token for token, _ in Counter(tokenize(text)).most_common(8)]
    result: list[str] = []
    for item in hits + tokens:
        if item and item not in result:
            result.append(item)
    return result[:5]


def fallback_classifier(paper: Paper, classifiers: list[str]) -> str:
    text = " ".join([paper.title, paper.abstract]).lower()
    best = ""
    best_hits = 0
    for classifier in classifiers:
        hits = sum(1 for token in tokenize(classifier) if token in text)
        if hits > best_hits:
            best, best_hits = classifier, hits
    if best:
        return best
    if "survey" in text or "review" in text:
        return "survey"
    if "benchmark" in text or "dataset" in text:
        return "benchmark"
    return "other"


def fallback_contribution(paper: Paper) -> str:
    if paper.abstract:
        return "摘要显示其主要价值在于：" + fallback_tldr(paper)
    return "当前只有元数据，需打开原文确认具体贡献。"
