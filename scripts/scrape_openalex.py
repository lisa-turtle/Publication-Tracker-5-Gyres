#!/usr/bin/env python3
"""Scrape/update 5 Gyres peer-reviewed publications from OpenAlex.

Outputs:
  data/publications.json
  data/metrics.json

Discovery strategy:
  1. Fetch manual DOI seeds from config.yaml.
  2. Search OpenAlex works for organization aliases and author names.
  3. Keep works that match configured org aliases in affiliations/text OR are manual DOI seeds.
  4. Deduplicate by DOI/OpenAlex ID and compute org h-index from cited_by_count.
"""
from __future__ import annotations

import json
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
CONFIG_PATH = ROOT / "config.yaml"
OPENALEX = "https://api.openalex.org/works"


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def normalize(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def inverted_index_to_text(index: dict[str, list[int]] | None) -> str:
    if not index:
        return ""
    pairs: list[tuple[int, str]] = []
    for word, positions in index.items():
        for pos in positions:
            pairs.append((pos, word))
    return " ".join(word for _, word in sorted(pairs))


def polite_params(config: dict[str, Any]) -> dict[str, str]:
    mailto = config.get("mailto")
    return {"mailto": mailto} if mailto else {}


def request_json(url: str, params: dict[str, Any]) -> dict[str, Any]:
    for attempt in range(4):
        r = requests.get(url, params=params, timeout=30)
        if r.status_code == 429:
            time.sleep(2 + attempt * 3)
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()
    return {}


def fetch_by_doi(doi: str, config: dict[str, Any]) -> dict[str, Any] | None:
    doi_clean = doi.strip().removeprefix("https://doi.org/").removeprefix("doi:")
    url = f"{OPENALEX}/https://doi.org/{doi_clean}"
    try:
        return request_json(url, polite_params(config))
    except requests.HTTPError:
        return None


def search_works(query: str, config: dict[str, Any], max_pages: int = 3) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    cursor = "*"
    for _ in range(max_pages):
        params = {
            "search": query,
            "per-page": 100,
            "cursor": cursor,
            "sort": "publication_year:desc",
            **polite_params(config),
        }
        data = request_json(OPENALEX, params)
        page = data.get("results", [])
        results.extend(page)
        cursor = data.get("meta", {}).get("next_cursor")
        if not cursor or not page:
            break
        time.sleep(0.15)
    return results


def work_text_blob(work: dict[str, Any]) -> str:
    parts = [work.get("title") or "", inverted_index_to_text(work.get("abstract_inverted_index"))]
    for authorship in work.get("authorships", []) or []:
        author = authorship.get("author", {}) or {}
        parts.append(author.get("display_name") or "")
        for inst in authorship.get("institutions", []) or []:
            parts.append(inst.get("display_name") or "")
        parts.append(authorship.get("raw_author_name") or "")
        parts.append(authorship.get("raw_affiliation_string") or "")
    return normalize(" ".join(parts))


def matches_org(work: dict[str, Any], aliases: list[str]) -> bool:
    blob = work_text_blob(work)
    return any(normalize(alias) in blob for alias in aliases)


def is_peer_reviewed_like(work: dict[str, Any], keep_types: set[str]) -> bool:
    typ = work.get("type")
    if typ not in keep_types:
        return False
    # Keep journal/proceedings records. Exclude obvious non-scholarly placeholders.
    title = normalize(work.get("title"))
    return bool(title) and title not in {"untitled"}


def simplify_work(work: dict[str, Any], manual: bool = False) -> dict[str, Any]:
    authors = []
    affiliations = []
    for authorship in work.get("authorships", []) or []:
        author_name = (authorship.get("author") or {}).get("display_name")
        if author_name:
            authors.append(author_name)
        raw_aff = authorship.get("raw_affiliation_string")
        if raw_aff:
            affiliations.append(raw_aff)
        for inst in authorship.get("institutions", []) or []:
            name = inst.get("display_name")
            if name:
                affiliations.append(name)

    primary = work.get("primary_location") or {}
    source = (primary.get("source") or {}).get("display_name")
    doi = work.get("doi")
    doi = doi.replace("https://doi.org/", "") if doi else None

    return {
        "id": work.get("id"),
        "doi": doi,
        "title": work.get("title"),
        "year": work.get("publication_year"),
        "publication_date": work.get("publication_date"),
        "type": work.get("type"),
        "journal_or_source": source,
        "authors": authors,
        "affiliations": sorted(set(affiliations)),
        "citation_count": int(work.get("cited_by_count") or 0),
        "openalex_url": work.get("id"),
        "landing_page_url": (primary.get("landing_page_url") or work.get("doi")),
        "is_open_access": (work.get("open_access") or {}).get("is_oa"),
        "manual_seed": manual,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def compute_metrics(publications: list[dict[str, Any]]) -> dict[str, Any]:
    citations = sorted([int(p.get("citation_count") or 0) for p in publications], reverse=True)
    h = 0
    for i, c in enumerate(citations, start=1):
        if c >= i:
            h = i
        else:
            break
    by_year = Counter(str(p.get("year") or "Unknown") for p in publications)
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "publication_count": len(publications),
        "total_citations": sum(citations),
        "h_index": h,
        "publications_by_year": dict(sorted(by_year.items())),
        "citation_source": "OpenAlex cited_by_count",
    }


def main() -> None:
    config = load_config()
    aliases = config["organization"]["aliases"]
    keep_types = set(config.get("openalex_types", []))

    exclude_dois = {
        doi.lower().replace("https://doi.org/", "").strip()
        for doi in config.get("exclude_dois", [])
    }

    found: dict[str, dict[str, Any]] = {}
    manual_ids: set[str] = set()

    for doi in config.get("manual_dois", []) or []:
        work = fetch_by_doi(doi, config)
        if work:
            key = work.get("doi") or work.get("id")
            found[key] = work
            manual_ids.add(key)

    queries = list(dict.fromkeys(aliases + config.get("authors", [])))
    for query in queries:
        for work in search_works(query, config):
            key = work.get("doi") or work.get("id")
            if key:
                found[key] = work

   simplified = []

for key, work in found.items():

    doi = (work.get("doi") or "")
    doi = doi.replace("https://doi.org/", "").lower().strip()

    # Skip excluded DOIs
    if doi in exclude_dois:
        continue

    manual = key in manual_ids

    if not manual:
        if not is_peer_reviewed_like(work, keep_types):
            continue
        if not matches_org(work, aliases):
            continue

    simplified.append(simplify_work(work, manual=manual))

    simplified.sort(key=lambda p: (p.get("year") or 0, p.get("citation_count") or 0), reverse=True)
    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "publications.json").write_text(json.dumps(simplified, indent=2, ensure_ascii=False), encoding="utf-8")
    (DATA_DIR / "metrics.json").write_text(json.dumps(compute_metrics(simplified), indent=2), encoding="utf-8")
    print(f"Saved {len(simplified)} publications")


if __name__ == "__main__":
    main()
