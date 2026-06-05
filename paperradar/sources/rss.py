from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import feedparser
import requests

from ..models import Paper


@dataclass
class FeedCandidate:
    url: str
    title: str = ""
    source_page: str = ""
    confidence: str = "low"
    reason: str = ""
    recent_titles: list[str] | None = None


def fetch_journal_rss(source_cfg: dict[str, Any], max_results: int = 20) -> list[Paper]:
    feeds = list(source_cfg.get("feeds") or [])
    papers: list[Paper] = []
    for feed in feeds:
        if isinstance(feed, str):
            feed_cfg = {"url": feed, "name": ""}
        else:
            feed_cfg = dict(feed)
        if feed_cfg.get("enabled", True) is False:
            continue
        url = str(feed_cfg.get("url") or "")
        if not url:
            continue
        parsed = feedparser.parse(url)
        feed_title = parsed.feed.get("title", feed_cfg.get("name", ""))
        for entry in parsed.entries[:max_results]:
            title = entry.get("title", "")
            doi = extract_doi(" ".join(str(entry.get(k, "")) for k in ["id", "link", "summary", "title"]))
            authors = [author.get("name", "") for author in entry.get("authors", [])] if entry.get("authors") else []
            papers.append(
                Paper(
                    id=str(entry.get("id") or entry.get("guid") or entry.get("link") or title),
                    title=title,
                    authors=[author for author in authors if author],
                    venue=str(feed_cfg.get("journal") or feed_title or feed_cfg.get("name") or ""),
                    abstract=str(entry.get("summary") or ""),
                    doi=doi,
                    url=str(entry.get("link") or ""),
                    source="journal_rss",
                    published_at=str(entry.get("published") or entry.get("updated") or ""),
                    updated_at=str(entry.get("updated") or ""),
                    extra={"feed_url": url, "feed_title": feed_title, "feed_type": feed_cfg.get("type", "unknown")},
                )
            )
    deduped: dict[str, Paper] = {}
    for paper in papers:
        if paper.title:
            deduped.setdefault(paper.normalized_key(), paper)
    return list(deduped.values())[:max_results]


def discover_feed_candidates(journal: str = "", homepage_url: str = "", timeout: int = 15) -> list[FeedCandidate]:
    candidates: list[FeedCandidate] = []
    pages: list[str] = []
    if homepage_url:
        pages.append(homepage_url)
    for page in pages:
        try:
            response = requests.get(page, timeout=timeout, headers={"User-Agent": "PaperRadar/0.1"})
            response.raise_for_status()
        except Exception as exc:
            candidates.append(FeedCandidate(url="", source_page=page, confidence="low", reason=f"page fetch failed: {exc}"))
            continue
        html = response.text
        for href, label in extract_feed_links(html):
            url = urljoin(page, href)
            candidate = validate_feed(url, source_page=page, journal=journal)
            candidate.reason = candidate.reason or label
            candidates.append(candidate)
    unique: dict[str, FeedCandidate] = {}
    for candidate in candidates:
        if candidate.url:
            unique.setdefault(candidate.url, candidate)
    return sorted(unique.values(), key=lambda item: {"high": 0, "medium": 1, "low": 2}.get(item.confidence, 2))


def extract_feed_links(html: str) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    rel_pattern = re.compile(r"<link[^>]+(?:rss|atom|application/rss\+xml|application/atom\+xml)[^>]+>", re.I)
    href_pattern = re.compile(r"href=[\"']([^\"']+)[\"']", re.I)
    for tag in rel_pattern.findall(html):
        match = href_pattern.search(tag)
        if match:
            links.append((match.group(1), "alternate feed link"))
    anchor_pattern = re.compile(r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
    for href, label in anchor_pattern.findall(html):
        text_label = re.sub(r"<[^>]+>", "", label).strip()
        haystack = f"{href} {text_label}".lower()
        if any(token in haystack for token in ["rss", "atom", "feed", "latest articles", "current issue"]):
            links.append((href, text_label or "feed link"))
    return links


def validate_feed(url: str, source_page: str = "", journal: str = "") -> FeedCandidate:
    parsed = feedparser.parse(url)
    title = parsed.feed.get("title", "")
    recent_titles = [entry.get("title", "") for entry in parsed.entries[:5] if entry.get("title")]
    confidence = "low"
    reason = "feed parsed"
    if parsed.bozo and not parsed.entries:
        return FeedCandidate(url=url, title=title, source_page=source_page, confidence="low", reason="feed parse failed")
    if recent_titles:
        confidence = "medium"
    if journal and title and journal.lower() in title.lower():
        confidence = "high"
        reason = "journal name matches feed title"
    return FeedCandidate(url=url, title=title, source_page=source_page, confidence=confidence, reason=reason, recent_titles=recent_titles)


def extract_doi(value: str) -> str:
    match = re.search(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", value, re.I)
    return match.group(0).rstrip(".,;") if match else ""
