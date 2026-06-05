from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote_plus

import requests

from ..models import Paper


def _year(value: Any) -> int | None:
    try:
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value[:4].isdigit():
            return int(value[:4])
    except Exception:
        return None
    return None


def fetch_openalex(query: str, max_results: int = 20, api_key: str = "") -> list[Paper]:
    if not query:
        return []
    url = f"https://api.openalex.org/works?search={quote_plus(query)}&per-page={max_results}"
    if api_key:
        url += f"&api_key={quote_plus(api_key)}"
    response = requests.get(url, timeout=20)
    response.raise_for_status()
    data = response.json()
    papers: list[Paper] = []
    for item in data.get("results", []):
        authors = [
            a.get("author", {}).get("display_name", "")
            for a in item.get("authorships", [])
            if a.get("author", {}).get("display_name")
        ]
        doi = str(item.get("doi") or "").replace("https://doi.org/", "")
        venue = (
            item.get("primary_location", {})
            .get("source", {})
            .get("display_name", "")
            or item.get("host_venue", {}).get("display_name", "")
            or ""
        )
        abstract = reconstruct_openalex_abstract(item.get("abstract_inverted_index") or {})
        papers.append(
            Paper(
                id=str(item.get("id") or doi or item.get("display_name")),
                title=str(item.get("display_name") or ""),
                authors=authors,
                year=_year(item.get("publication_year")),
                venue=venue,
                abstract=abstract,
                doi=doi,
                url=str(item.get("id") or item.get("primary_location", {}).get("landing_page_url") or ""),
                pdf_url=str(item.get("primary_location", {}).get("pdf_url") or ""),
                source="openalex",
                published_at=str(item.get("publication_date") or ""),
                extra={"cited_by_count": item.get("cited_by_count", 0)},
            )
        )
    return [paper for paper in papers if paper.title]


def reconstruct_openalex_abstract(index: dict[str, list[int]]) -> str:
    if not index:
        return ""
    positions: dict[int, str] = {}
    for word, locs in index.items():
        for loc in locs:
            positions[int(loc)] = word
    return " ".join(positions[i] for i in sorted(positions))


def fetch_crossref(query: str, max_results: int = 20, email: str = "") -> list[Paper]:
    if not query:
        return []
    url = f"https://api.crossref.org/works?query={quote_plus(query)}&rows={max_results}"
    headers = {"User-Agent": f"PaperRadar/0.1 ({email})"} if email else {"User-Agent": "PaperRadar/0.1"}
    response = requests.get(url, headers=headers, timeout=20)
    response.raise_for_status()
    items = response.json().get("message", {}).get("items", [])
    papers: list[Paper] = []
    for item in items:
        title = " ".join(item.get("title") or [])
        authors = [
            " ".join(part for part in [author.get("given", ""), author.get("family", "")] if part)
            for author in item.get("author", [])
        ]
        year = None
        parts = item.get("published-print", {}).get("date-parts") or item.get("published-online", {}).get("date-parts")
        if parts and parts[0]:
            year = _year(parts[0][0])
        papers.append(
            Paper(
                id=str(item.get("DOI") or title),
                title=title,
                authors=[author for author in authors if author],
                year=year,
                venue=" ".join(item.get("container-title") or []),
                abstract=strip_tags(str(item.get("abstract") or "")),
                doi=str(item.get("DOI") or ""),
                url=str(item.get("URL") or ""),
                source="crossref",
                published_at=str(item.get("published", {}).get("date-parts", "")),
                extra={"type": item.get("type", "")},
            )
        )
    return [paper for paper in papers if paper.title]


def strip_tags(value: str) -> str:
    return re.sub(r"<[^>]+>", " ", value).replace("\n", " ").strip()


def fetch_scholarly(settings: dict[str, Any], source_cfg: dict[str, Any], max_results: int = 20) -> list[Paper]:
    query = str(source_cfg.get("query") or "").strip()
    if not query:
        return []
    sources = settings.get("sources", {})
    errors: list[str] = []
    papers: list[Paper] = []
    if sources.get("openalex", {}).get("enabled", True):
        try:
            papers.extend(
                fetch_openalex(query, max_results=max_results, api_key=sources.get("openalex", {}).get("api_key", ""))
            )
        except Exception as exc:
            errors.append(f"OpenAlex failed: {exc}")
    if len(papers) < max_results and sources.get("crossref", {}).get("enabled", True):
        try:
            papers.extend(fetch_crossref(query, max_results=max_results - len(papers), email=sources.get("crossref", {}).get("email", "")))
        except Exception as exc:
            errors.append(f"Crossref failed: {exc}")
    deduped: dict[str, Paper] = {}
    for paper in papers:
        deduped.setdefault(paper.normalized_key(), paper)
    result = list(deduped.values())[:max_results]
    if errors and not result:
        result.append(
            Paper(
                id="source-error",
                title="No papers fetched because scholarly sources failed",
                abstract="; ".join(errors),
                source="diagnostic",
            )
        )
    return result
