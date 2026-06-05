from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date, datetime, time, timedelta
from typing import Any
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import feedparser
import requests

from ..models import Paper

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"


def fetch_arxiv(source_cfg: dict[str, Any], topic_keywords: list[str], max_results: int = 20) -> list[Paper]:
    categories = list(source_cfg.get("categories") or [])
    query = str(source_cfg.get("query") or "").strip()
    mode = str(source_cfg.get("mode") or "latest")
    if mode == "daily_window" and categories:
        rss_papers = fetch_arxiv_rss(categories, source_cfg, max_results)
        if rss_papers:
            return rss_papers
    terms: list[str] = []
    if categories:
        category_query = " OR ".join(f"cat:{cat}" for cat in categories)
        terms.append(f"({category_query})" if len(categories) > 1 else category_query)
    if query and mode != "daily_window":
        terms.append(f'all:"{query}"')
    elif topic_keywords and mode not in {"daily_window", "category_latest"}:
        terms.append(" OR ".join(f'all:"{keyword}"' for keyword in topic_keywords[:5]))
    search_query = " AND ".join(terms) if terms else "cat:cs.AI"
    url = (
        "https://export.arxiv.org/api/query?"
        f"search_query={quote_plus(search_query)}&sortBy=submittedDate&sortOrder=descending&max_results={max_results}"
    )
    response = get_with_retries(url, timeout=45)
    root = ET.fromstring(response.text)
    papers: list[Paper] = []
    window = announcement_window(source_cfg) if mode == "daily_window" else None
    for entry in root.findall(f"{ATOM}entry"):
        title = text(entry, f"{ATOM}title")
        arxiv_url = text(entry, f"{ATOM}id")
        arxiv_id = arxiv_url.rsplit("/", 1)[-1] if arxiv_url else ""
        authors = [text(author, f"{ATOM}name") for author in entry.findall(f"{ATOM}author")]
        pdf_url = ""
        for link in entry.findall(f"{ATOM}link"):
            if link.attrib.get("title") == "pdf" or link.attrib.get("type") == "application/pdf":
                pdf_url = link.attrib.get("href", "")
        category = ""
        primary = entry.find(f"{ARXIV}primary_category")
        if primary is not None:
            category = primary.attrib.get("term", "")
        published_at = text(entry, f"{ATOM}published")
        if window and not in_window(published_at, window):
            continue
        papers.append(
            Paper(
                id=arxiv_id or title,
                title=" ".join(title.split()),
                authors=[author for author in authors if author],
                abstract=" ".join(text(entry, f"{ATOM}summary").split()),
                arxiv_id=arxiv_id,
                url=arxiv_url,
                pdf_url=pdf_url,
                source="arxiv",
                published_at=published_at,
                updated_at=text(entry, f"{ATOM}updated"),
                extra={"category": category},
            )
        )
    return [paper for paper in papers if paper.title]


def fetch_arxiv_rss(categories: list[str], source_cfg: dict[str, Any], max_results: int) -> list[Paper]:
    query = "+".join(categories)
    url = f"https://rss.arxiv.org/atom/{query}"
    try:
        response = get_with_retries(url, timeout=35)
        feed = feedparser.parse(response.content)
    except Exception:
        return []
    if getattr(feed, "bozo", False) and not feed.entries:
        return []
    allowed = {"new", "replace"}
    if source_cfg.get("include_cross_list"):
        allowed.add("cross")
    papers: list[Paper] = []
    for entry in feed.entries[:max_results]:
        announce_type = str(entry.get("arxiv_announce_type") or "new")
        if announce_type not in allowed:
            continue
        arxiv_id = extract_arxiv_id_from_entry(entry)
        authors = [author.get("name", "") for author in entry.get("authors", []) if author.get("name")]
        pdf_url = ""
        for link in entry.get("links", []) or []:
            href = link.get("href", "")
            if link.get("type") == "application/pdf" or "/pdf/" in href:
                pdf_url = href
        if not pdf_url and arxiv_id:
            pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
        category = ""
        tags = entry.get("tags", []) or []
        if tags:
            category = tags[0].get("term", "")
        paper = Paper(
            id=arxiv_id or str(entry.get("id") or entry.get("link") or entry.get("title")),
            title=" ".join(str(entry.get("title") or "").split()),
            authors=authors,
            abstract=" ".join(str(entry.get("summary") or "").split()),
            arxiv_id=arxiv_id,
            url=str(entry.get("link") or f"https://arxiv.org/abs/{arxiv_id}"),
            pdf_url=pdf_url,
            source="arxiv",
            published_at=str(entry.get("published") or ""),
            updated_at=str(entry.get("updated") or ""),
            extra={"category": category, "announce_type": announce_type},
        )
        if paper.title:
            papers.append(paper)
    return papers


def get_with_retries(url: str, timeout: int) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = requests.get(url, timeout=timeout, headers={"User-Agent": "PaperRadar/0.1"})
            response.raise_for_status()
            return response
        except Exception as exc:
            last_error = exc
            if attempt == 2:
                break
    if last_error:
        raise last_error
    raise RuntimeError("request failed")


def extract_arxiv_id_from_entry(entry: Any) -> str:
    raw = str(entry.get("id") or entry.get("link") or "")
    if "oai:arXiv.org:" in raw:
        return raw.rsplit(":", 1)[-1]
    if "/abs/" in raw:
        return raw.rsplit("/abs/", 1)[-1]
    import re

    match = re.search(r"\d{4}\.\d{4,5}(v\d+)?", raw)
    return match.group(0) if match else ""


def text(node: ET.Element, name: str) -> str:
    child = node.find(name)
    return child.text.strip() if child is not None and child.text else ""


def announcement_window(source_cfg: dict[str, Any]) -> tuple[datetime, datetime]:
    tz = ZoneInfo("America/New_York")
    if source_cfg.get("announcement_date"):
        announcement_date = date.fromisoformat(str(source_cfg["announcement_date"]))
    else:
        now_et = datetime.now(tz)
        announcement_date = effective_announcement_date(now_et)
    day_deltas = {
        0: (4, 3),
        1: (4, 1),
        2: (2, 1),
        3: (2, 1),
        4: (2, 1),
    }
    if announcement_date.weekday() not in day_deltas:
        announcement_date = effective_announcement_date(datetime.combine(announcement_date, time(23, 0), tzinfo=tz))
    start_back, end_back = day_deltas[announcement_date.weekday()]
    start_day = announcement_date - timedelta(days=start_back)
    end_day = announcement_date - timedelta(days=end_back)
    return datetime.combine(start_day, time(14, 0), tzinfo=tz), datetime.combine(end_day, time(14, 0), tzinfo=tz)


def effective_announcement_date(now_et: datetime) -> date:
    if now_et.weekday() < 5 and now_et.time() >= time(20, 0):
        return now_et.date()
    candidate = now_et.date() - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def in_window(value: str, window: tuple[datetime, datetime]) -> bool:
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York"))
    except Exception:
        return False
    return window[0] <= parsed < window[1]
