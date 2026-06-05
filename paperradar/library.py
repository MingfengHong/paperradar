from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any

import requests

from .models import LibraryItem, slugify


def load_library(config: dict[str, Any], root: Path) -> list[LibraryItem]:
    items: list[LibraryItem] = []
    for raw in config.get("items", []) or []:
        title = str(raw.get("title") or "")
        if title:
            items.append(
                LibraryItem(
                    id=str(raw.get("id") or slugify(title)),
                    title=title,
                    authors=list(raw.get("authors") or []),
                    year=as_int(raw.get("year")),
                    doi=str(raw.get("doi") or ""),
                    arxiv_id=str(raw.get("arxiv_id") or ""),
                    venue=str(raw.get("venue") or ""),
                    tags=list(raw.get("tags") or []),
                    collection=str(raw.get("collection") or ""),
                    collection_paths=list(raw.get("collection_paths") or []),
                    note=str(raw.get("note") or ""),
                    added_at=str(raw.get("added_at") or raw.get("date_added") or ""),
                )
            )
    for import_cfg in config.get("imports", []) or []:
        path = root / str(import_cfg.get("path") or "")
        if not path.exists():
            continue
        kind = str(import_cfg.get("type") or path.suffix.lstrip(".")).lower()
        if kind == "csv":
            items.extend(load_csv(path))
        elif kind in {"bib", "bibtex"}:
            items.extend(load_bibtex(path))
        elif kind == "ris":
            items.extend(load_ris(path))
    deduped: dict[str, LibraryItem] = {}
    for item in items:
        deduped.setdefault(item.normalized_key(), item)
    return list(deduped.values())


def load_zotero(zotero_config: dict[str, Any], limit: int = 200) -> list[LibraryItem]:
    cfg = zotero_config.get("zotero", zotero_config) or {}
    if not cfg.get("enabled"):
        return []
    api_key = str(cfg.get("api_key") or "")
    user_id = str(cfg.get("user_id") or "")
    group_id = str(cfg.get("group_id") or "")
    if not api_key or not (user_id or group_id):
        return []
    base = f"https://api.zotero.org/groups/{group_id}" if group_id else f"https://api.zotero.org/users/{user_id}"
    params = {"format": "json", "limit": min(limit, 100), "itemType": "-attachment || note", "sort": "dateAdded", "direction": "desc"}
    headers = {"Authorization": f"Bearer {api_key}"}
    collections = zotero_collections(base, headers)
    items: list[LibraryItem] = []
    start = 0
    while len(items) < limit:
        params["start"] = start
        response = requests.get(f"{base}/items", params=params, headers=headers, timeout=25)
        response.raise_for_status()
        payload = response.json()
        if not payload:
            break
        for raw in payload:
            data = raw.get("data", {})
            title = data.get("title") or ""
            if not title:
                continue
            creators = data.get("creators") or []
            authors = [
                " ".join(part for part in [creator.get("firstName", ""), creator.get("lastName", "")] if part)
                or creator.get("name", "")
                for creator in creators
            ]
            tags = [tag.get("tag", "") for tag in data.get("tags", []) if tag.get("tag")]
            collection_keys = list(data.get("collections") or [])
            collection_paths = [collections.get(key, key) for key in collection_keys]
            if not zotero_item_allowed(tags, collection_paths, cfg):
                continue
            items.append(
                LibraryItem(
                    id=data.get("key") or slugify(title),
                    title=title,
                    authors=[author for author in authors if author],
                    year=as_int(data.get("date")),
                    doi=data.get("DOI") or "",
                    arxiv_id=extract_arxiv_id(" ".join([data.get("url", ""), data.get("extra", "")])),
                    venue=data.get("publicationTitle") or data.get("conferenceName") or "",
                    tags=tags,
                    collection="; ".join(collection_paths) or "zotero",
                    collection_paths=collection_paths,
                    note=data.get("abstractNote") or "",
                    added_at=data.get("dateAdded") or "",
                )
            )
        if len(payload) < params["limit"]:
            break
        start += params["limit"]
    return items[:limit]


def load_csv(path: Path) -> list[LibraryItem]:
    items: list[LibraryItem] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            title = row.get("title") or row.get("Title") or ""
            if not title:
                continue
            items.append(
                LibraryItem(
                    id=row.get("id") or slugify(title),
                    title=title,
                    authors=split_authors(row.get("authors") or row.get("Authors") or ""),
                    year=as_int(row.get("year") or row.get("Year")),
                    doi=row.get("doi") or row.get("DOI") or "",
                    arxiv_id=row.get("arxiv_id") or row.get("arXiv") or "",
                    venue=row.get("venue") or row.get("Venue") or "",
                    tags=split_tags(row.get("tags") or row.get("Tags") or ""),
                    collection=row.get("collection") or row.get("Collection") or "",
                    collection_paths=split_tags(row.get("collection_paths") or row.get("Collection Paths") or ""),
                    note=row.get("abstract") or row.get("Abstract") or row.get("note") or "",
                    added_at=row.get("added_at") or row.get("dateAdded") or row.get("Date Added") or "",
                )
            )
    return items


def load_bibtex(path: Path) -> list[LibraryItem]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    entries = re.split(r"\n@", text)
    items: list[LibraryItem] = []
    for entry in entries:
        title = field_value(entry, "title")
        if not title:
            continue
        doi = field_value(entry, "doi")
        items.append(
            LibraryItem(
                id=slugify(doi or title),
                title=clean_bib_value(title),
                authors=split_authors(clean_bib_value(field_value(entry, "author")).replace(" and ", ";")),
                year=as_int(field_value(entry, "year")),
                doi=clean_bib_value(doi),
                venue=clean_bib_value(field_value(entry, "journal") or field_value(entry, "booktitle")),
                note=clean_bib_value(field_value(entry, "abstract")),
            )
        )
    return items


def load_ris(path: Path) -> list[LibraryItem]:
    items: list[LibraryItem] = []
    current: dict[str, list[str]] = {}
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if len(line) < 6 or " - " not in line[:6]:
            continue
        key, value = line[:2], line[6:].strip()
        if key == "ER":
            title = first(current, "TI", "T1")
            if title:
                items.append(
                    LibraryItem(
                        id=slugify(first(current, "DO") or title),
                        title=title,
                        authors=current.get("AU", []),
                        year=as_int(first(current, "PY", "Y1")),
                        doi=first(current, "DO"),
                        venue=first(current, "JO", "JF"),
                        note=first(current, "AB", "N2"),
                    )
                )
            current = {}
        else:
            current.setdefault(key, []).append(value)
    return items


def field_value(entry: str, field: str) -> str:
    match = re.search(rf"{field}\s*=\s*[\{{\"](.+?)[\}}\"]\s*,", entry, re.I | re.S)
    return match.group(1).strip() if match else ""


def clean_bib_value(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("{", "").replace("}", "")).strip()


def split_authors(value: str) -> list[str]:
    return [part.strip() for part in re.split(r";|,", value) if part.strip()]


def split_tags(value: str) -> list[str]:
    return [part.strip() for part in re.split(r";|,", value) if part.strip()]


def first(mapping: dict[str, list[str]], *keys: str) -> str:
    for key in keys:
        values = mapping.get(key) or []
        if values:
            return values[0]
    return ""


def as_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(str(value)[:4])
    except Exception:
        return None


def extract_arxiv_id(value: str) -> str:
    match = re.search(r"arxiv[:/ ]+([0-9]{4}\.[0-9]{4,5}(v\d+)?)", value, re.I)
    return match.group(1) if match else ""


def zotero_collections(base: str, headers: dict[str, str]) -> dict[str, str]:
    try:
        response = requests.get(f"{base}/collections", params={"format": "json", "limit": 100}, headers=headers, timeout=25)
        response.raise_for_status()
        raw = response.json()
    except Exception:
        return {}
    by_key = {item.get("key"): item for item in raw if item.get("key")}

    def path_for(key: str) -> str:
        item = by_key.get(key, {})
        data = item.get("data", {})
        name = data.get("name") or key
        parent = data.get("parentCollection")
        return f"{path_for(parent)}/{name}" if parent and parent in by_key else name

    return {key: path_for(key) for key in by_key}


def zotero_item_allowed(tags: list[str], collection_paths: list[str], cfg: dict[str, Any]) -> bool:
    include_tags = set(str(tag).lower() for tag in cfg.get("tags") or [])
    include_collections = [str(value).lower() for value in cfg.get("collections") or cfg.get("include_path") or []]
    ignore_collections = [str(value).lower() for value in cfg.get("ignore_path") or []]
    tag_set = set(tag.lower() for tag in tags)
    path_text = "\n".join(collection_paths).lower()
    if include_tags and not (include_tags & tag_set):
        return False
    if include_collections and not any(match_path(path_text, pattern) for pattern in include_collections):
        return False
    if ignore_collections and any(match_path(path_text, pattern) for pattern in ignore_collections):
        return False
    return True


def match_path(path_text: str, pattern: str) -> bool:
    if not pattern:
        return False
    if pattern in path_text:
        return True
    regex = re.escape(pattern).replace("\\*\\*", ".*").replace("\\*", "[^/]*")
    return re.search(regex, path_text) is not None
