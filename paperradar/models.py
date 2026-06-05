from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def slugify(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in value.strip())
    parts = [part for part in cleaned.split("-") if part]
    return "-".join(parts) or "item"


@dataclass
class Topic:
    id: str
    name: str
    research_question: str = ""
    keywords: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)
    positive_papers: list[str] = field(default_factory=list)
    negative_papers: list[str] = field(default_factory=list)
    venues: list[str] = field(default_factory=list)
    reading_goal: str = ""
    status: str = "active"
    library_tags: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Topic":
        name = str(data.get("name") or data.get("id") or "Research Topic")
        return cls(
            id=str(data.get("id") or slugify(name)),
            name=name,
            research_question=str(data.get("research_question") or data.get("question") or ""),
            keywords=list(data.get("keywords") or []),
            exclude_keywords=list(data.get("exclude_keywords") or data.get("exclude") or []),
            positive_papers=list(data.get("positive_papers") or []),
            negative_papers=list(data.get("negative_papers") or []),
            venues=list(data.get("venues") or []),
            reading_goal=str(data.get("reading_goal") or ""),
            status=str(data.get("status") or "active"),
            library_tags=list(data.get("library_tags") or []),
        )


@dataclass
class Subscription:
    id: str
    topic_id: str
    type: str
    enabled: bool = True
    report_modules: list[str] = field(default_factory=lambda: ["paper_digest"])
    schedule: str = "manual"
    timezone: str = "Asia/Shanghai"
    max_papers: int = 10
    min_score: float = 0.55
    channels: list[str] = field(default_factory=list)
    source: dict[str, Any] = field(default_factory=dict)
    analysis_depth: str = "standard"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Subscription":
        sid = str(data.get("id") or slugify(f"{data.get('topic_id', 'topic')}-{data.get('type', 'paper')}"))
        return cls(
            id=sid,
            topic_id=str(data.get("topic_id") or data.get("topic") or "default"),
            type=str(data.get("type") or "paper"),
            enabled=bool(data.get("enabled", True)),
            report_modules=list(data.get("report_modules") or ["paper_digest"]),
            schedule=str(data.get("schedule") or "manual"),
            timezone=str(data.get("timezone") or "Asia/Shanghai"),
            max_papers=int(data.get("max_papers") or 10),
            min_score=float(data.get("min_score") or 0.55),
            channels=list(data.get("channels") or []),
            source=dict(data.get("source") or {}),
            analysis_depth=str(data.get("analysis_depth") or "standard"),
        )


@dataclass
class Paper:
    id: str
    title: str
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    venue: str = ""
    abstract: str = ""
    doi: str = ""
    arxiv_id: str = ""
    url: str = ""
    pdf_url: str = ""
    source: str = ""
    published_at: str = ""
    updated_at: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def normalized_key(self) -> str:
        if self.doi:
            return f"doi:{self.doi.lower().strip()}"
        if self.arxiv_id:
            return f"arxiv:{self.arxiv_id.lower().strip()}"
        if self.url:
            return f"url:{self.url.lower().strip().rstrip('/')}"
        normalized_title = " ".join(self.title.lower().split())
        return f"title:{normalized_title}"


@dataclass
class LibraryItem:
    id: str
    title: str
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    doi: str = ""
    arxiv_id: str = ""
    venue: str = ""
    tags: list[str] = field(default_factory=list)
    collection: str = ""
    collection_paths: list[str] = field(default_factory=list)
    note: str = ""
    added_at: str = ""

    def normalized_key(self) -> str:
        if self.doi:
            return f"doi:{self.doi.lower().strip()}"
        if self.arxiv_id:
            return f"arxiv:{self.arxiv_id.lower().strip()}"
        return f"title:{' '.join(self.title.lower().split())}"


@dataclass
class Recommendation:
    paper: Paper
    worth_read_score: float
    relevance_score: float
    novelty_score: float
    utility_score: float
    urgency_score: float
    confidence_score: float
    reading_action: str
    reason: str
    evidence_level: str = "metadata"
    related_library_items: list[str] = field(default_factory=list)
    tldr: str = ""
    keywords: list[str] = field(default_factory=list)
    classifier: str = ""
    contribution: str = ""
    limitation: str = ""
    library_similarity_score: float = 0.0
    filtered: bool = False

    def total_score(self) -> float:
        return round(
            0.36 * self.worth_read_score
            + 0.28 * self.relevance_score
            + 0.16 * self.utility_score
            + 0.10 * self.novelty_score
            + 0.10 * self.urgency_score,
            4,
        )


@dataclass
class RunResult:
    run_id: str
    topic: Topic
    subscription: Subscription
    candidates: list[Paper]
    recommendations: list[Recommendation]
    report_markdown: str
    report_html: str
    created_at: str = field(default_factory=utc_now)
