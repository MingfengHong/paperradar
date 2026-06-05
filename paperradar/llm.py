from __future__ import annotations

import json
from typing import Any

import requests

from .models import Paper, Recommendation, Subscription, Topic


class LLMClient:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config or {}
        self.enabled = bool(self.config.get("enabled") and self.config.get("api_key"))
        self.base_url = str(self.config.get("base_url") or "https://api.openai.com/v1").rstrip("/")
        self.model = str(self.config.get("model") or "gpt-4o-mini")
        self.api_key = str(self.config.get("api_key") or "")

    def require_configured(self) -> None:
        if not self.enabled:
            raise RuntimeError(
                "LLM is required for PaperRadar runs. Set LLM_API_KEY, LLM_BASE_URL and LLM_MODEL, "
                "or configure config/settings.yaml llm.api_key."
            )

    def plan_scholarly_queries(self, topic: Topic, subscription: Subscription) -> list[str]:
        self.require_configured()
        base_query = str(subscription.source.get("query") or "").strip()
        response = requests.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json=self._build_query_plan_payload(topic, subscription),
            timeout=40,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        parsed = json.loads(extract_json(content))
        queries = parsed.get("queries", [])
        if isinstance(queries, list):
            result = [normalize_query(str(query)) for query in queries if normalize_query(str(query))]
            if result:
                return dedupe_strings(result)[:5]
        fallback = base_query or " ".join([topic.research_question, " ".join(topic.keywords)]).strip()
        if fallback:
            return [fallback]
        raise RuntimeError("LLM query planner returned no usable search queries.")

    def score_papers(self, topic: Topic, papers: list[Paper], heuristic: list[Recommendation]) -> list[Recommendation]:
        if not papers:
            return heuristic
        if not self.enabled:
            return heuristic
        try:
            payload = self._build_payload(topic, papers, heuristic)
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=40,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            parsed = json.loads(extract_json(content))
            by_key = {rec.paper.normalized_key(): rec for rec in heuristic}
            for item in parsed.get("papers", []):
                key = str(item.get("key") or "")
                rec = by_key.get(key)
                if not rec:
                    continue
                rec.worth_read_score = clamp(item.get("worth_read_score"), rec.worth_read_score)
                rec.relevance_score = clamp(item.get("relevance_score"), rec.relevance_score)
                rec.utility_score = clamp(item.get("utility_score"), rec.utility_score)
                rec.novelty_score = clamp(item.get("novelty_score"), rec.novelty_score)
                rec.urgency_score = clamp(item.get("urgency_score"), rec.urgency_score)
                rec.confidence_score = clamp(item.get("confidence_score"), rec.confidence_score)
                rec.reading_action = str(item.get("reading_action") or rec.reading_action)
                rec.reason = str(item.get("reason") or rec.reason)
                rec.tldr = str(item.get("tldr") or rec.tldr)
                rec.keywords = [str(value) for value in item.get("keywords", rec.keywords) or []][:6]
                rec.classifier = str(item.get("classifier") or rec.classifier)
                rec.contribution = str(item.get("contribution") or rec.contribution)
                rec.limitation = str(item.get("limitation") or rec.limitation)
            return list(by_key.values())
        except Exception:
            return heuristic

    def analyze_recommendations(self, topic: Topic, recommendations: list[Recommendation], ranking_config: dict[str, Any]) -> list[Recommendation]:
        if not recommendations:
            return recommendations
        if not self.enabled:
            return recommendations
        analysis_cfg = ranking_config.get("llm_analysis", {})
        if analysis_cfg.get("enabled", True) is False:
            return recommendations
        max_papers = int(analysis_cfg.get("max_papers") or 12)
        target = sorted(recommendations, key=lambda rec: rec.total_score(), reverse=True)[:max_papers]
        try:
            payload = self._build_analysis_payload(topic, target, ranking_config)
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=60,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            parsed = json.loads(extract_json(content))
            by_key = {rec.paper.normalized_key(): rec for rec in recommendations}
            for item in parsed.get("papers", []):
                rec = by_key.get(str(item.get("key") or ""))
                if not rec:
                    continue
                rec.tldr = str(item.get("tldr") or rec.tldr)
                rec.keywords = [str(value) for value in item.get("keywords", rec.keywords) or []][:6]
                rec.classifier = str(item.get("classifier") or rec.classifier)
                rec.contribution = str(item.get("contribution") or rec.contribution)
                rec.limitation = str(item.get("limitation") or rec.limitation)
                rec.reason = str(item.get("reason") or rec.reason)
            return recommendations
        except Exception:
            return recommendations

    def _build_payload(self, topic: Topic, papers: list[Paper], heuristic: list[Recommendation]) -> dict[str, Any]:
        rows = []
        hmap = {rec.paper.normalized_key(): rec for rec in heuristic}
        for paper in papers[:20]:
            rec = hmap.get(paper.normalized_key())
            rows.append(
                {
                    "key": paper.normalized_key(),
                    "title": paper.title,
                    "abstract": paper.abstract[:1200],
                    "venue": paper.venue,
                    "source": paper.source,
                    "heuristic_action": rec.reading_action if rec else "",
                    "heuristic_reason": rec.reason if rec else "",
                }
            )
        system = (
            "You are PaperRadar's paper triage assistant. Score papers for a specific research topic. "
            "Return strict JSON only. Do not invent facts beyond title/metadata/abstract."
        )
        user = {
            "topic": topic.__dict__,
            "papers": rows,
            "schema": {
                "papers": [
                    {
                        "key": "paper normalized key",
                        "worth_read_score": 0.0,
                        "relevance_score": 0.0,
                        "novelty_score": 0.0,
                        "utility_score": 0.0,
                        "urgency_score": 0.0,
                        "confidence_score": 0.0,
                        "reading_action": "精读|略读|收藏|观察|过滤",
                        "reason": "short Chinese explanation",
                        "tldr": "1-2 sentence technical summary in Chinese",
                        "keywords": ["keyword"],
                        "classifier": "one classifier",
                        "contribution": "main contribution",
                        "limitation": "what is uncertain from provided metadata",
                    }
                ]
            },
        }
        return {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(user, ensure_ascii=False)}],
            "temperature": 0.2,
        }

    def _build_query_plan_payload(self, topic: Topic, subscription: Subscription) -> dict[str, Any]:
        system = (
            "You are PaperRadar's scholarly search planner. Generate precise search expressions for OpenAlex and Crossref. "
            "Return strict JSON only. Do not include explanations."
        )
        user = {
            "topic": topic.__dict__,
            "subscription": {
                "id": subscription.id,
                "type": subscription.type,
                "source": subscription.source,
                "max_papers": subscription.max_papers,
            },
            "requirements": [
                "Generate 3 to 5 concise scholarly search queries.",
                "Each query should be suitable for metadata search in OpenAlex/Crossref.",
                "Prefer key concepts, synonyms, and method terms over long natural-language sentences.",
                "Avoid Boolean syntax that public APIs may parse poorly.",
                "Honor exclude_keywords by avoiding those concepts.",
            ],
            "schema": {"queries": ["query string"]},
        }
        return {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(user, ensure_ascii=False)}],
            "temperature": 0.2,
        }

    def _build_analysis_payload(self, topic: Topic, recommendations: list[Recommendation], ranking_config: dict[str, Any]) -> dict[str, Any]:
        language = ranking_config.get("llm_analysis", {}).get("language", "Chinese")
        classifiers = ranking_config.get("classifiers") or ["survey", "benchmark", "method", "system", "dataset", "application", "theory", "evaluation", "tooling", "other"]
        rows = []
        for rec in recommendations:
            paper = rec.paper
            rows.append(
                {
                    "key": paper.normalized_key(),
                    "title": paper.title,
                    "abstract": paper.abstract[:1800],
                    "authors": paper.authors[:8],
                    "venue": paper.venue,
                    "source": paper.source,
                    "year": paper.year,
                    "relevance_score": rec.relevance_score,
                    "library_similarity_score": rec.library_similarity_score,
                    "related_library_items": rec.related_library_items,
                }
            )
        system = (
            "You are PaperRadar's scientific paper analyst. "
            "Analyze only the provided title, abstract and metadata. "
            "Return strict JSON only; do not invent experiments, citations, author reputation, or venue metrics."
        )
        user = {
            "language": language,
            "topic": topic.__dict__,
            "classifiers": classifiers,
            "papers": rows,
            "schema": {
                "papers": [
                    {
                        "key": "paper normalized key",
                        "tldr": "1-2 sentence TL;DR",
                        "keywords": ["3-6 normalized keywords"],
                        "classifier": "exactly one classifier from provided list when possible",
                        "contribution": "main technical contribution or value",
                        "limitation": "uncertainty or caveat visible from available evidence",
                        "reason": "why this paper is or is not worth reading for the user",
                    }
                ]
            },
        }
        return {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(user, ensure_ascii=False)}],
            "temperature": 0.1,
        }


def clamp(value: Any, fallback: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return fallback


def extract_json(content: str) -> str:
    content = content.strip()
    if content.startswith("```"):
        content = content.strip("`")
        if content.startswith("json"):
            content = content[4:].strip()
    start = content.find("{")
    end = content.rfind("}")
    return content[start : end + 1] if start >= 0 and end >= 0 else content


def normalize_query(value: str) -> str:
    return " ".join(value.replace("\n", " ").split()).strip(" \"'")


def dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = value.lower()
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result
