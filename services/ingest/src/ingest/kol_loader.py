"""KOL (Key Opinion Leader) list loader for filtered social ingestion (M4.1).

The config maps category names to author identifiers. The loader resolves
the file once and produces a case-insensitive index from author identifier
to the sorted list of categories the author belongs to. Workers consume
the index to stamp ``metadata.kol_categories`` and ``metadata.is_kol`` on
matched posts so downstream social-feature aggregation can weight KOL-
tagged posts.

Unset path / missing file / malformed JSON return an empty index — the
caller no-ops the KOL stamping path and behaves identically to a worker
without KOL config. This makes the feature opt-in by configuration.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def load_kol_index(path: str | None) -> dict[str, list[str]]:
    """Load the KOL config and return a case-insensitive author → categories index.

    The author keys are lowercased. The category lists are sorted and
    deduplicated. An author appearing in multiple categories is indexed
    against all of them.
    """
    if not path:
        return {}
    file_path = Path(path)
    if not file_path.is_file():
        return {}
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("kol_loader.parse_failed path=%s error=%s", path, exc)
        return {}
    if not isinstance(payload, dict):
        return {}
    raw_categories = payload.get("categories")
    if not isinstance(raw_categories, dict):
        return {}
    index: dict[str, set[str]] = {}
    for category, authors in raw_categories.items():
        if not isinstance(category, str) or not category.strip():
            continue
        if not isinstance(authors, list):
            continue
        normalized_category = category.strip()
        for author in authors:
            if not isinstance(author, str):
                continue
            normalized = author.strip().lower()
            if not normalized:
                continue
            index.setdefault(normalized, set()).add(normalized_category)
    return {author: sorted(categories) for author, categories in index.items()}


def kol_categories_for_author(
    author: str | None,
    index: dict[str, list[str]] | None,
) -> list[str]:
    """Return the sorted KOL categories for an author, or [] when no match."""
    if not author or not index:
        return []
    return list(index.get(author.strip().lower(), []))
