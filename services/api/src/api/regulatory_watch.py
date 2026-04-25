"""Regulatory-watch reader (PRD §9 cross-cutting).

Wraps :func:`model.filter_regulatory_news` over the ``external_events``
news/social feed so an operator can see which already-ingested headlines
match prediction-market-relevant regulatory keywords. The reader is
read-only — it does NOT itself ingest, alert, or persist; the
ingestion is handled by ``services/ingest/src/ingest/workers/rss_ingest.py``
and ``reddit_ingest.py``, this module just queries that table.

Default keyword list mirrors :func:`filter_regulatory_news`'s built-in:
prediction-market, gambling, CFTC, SEC, FCA, regulation. Operators can
override via the ``keywords`` query param.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from clickhouse_connect.driver.asyncclient import AsyncClient
from model import filter_regulatory_news

from .external_events import ExternalEvent, list_external_events


@dataclass(frozen=True)
class RegulatoryMatch:
    event: ExternalEvent
    matched_keywords: tuple[str, ...]


_DEFAULT_KEYWORDS: tuple[str, ...] = (
    "prediction market",
    "gambling",
    "CFTC",
    "SEC",
    "FCA",
    "regulation",
)


def _normalize_keywords(keywords: Sequence[str] | None) -> tuple[str, ...]:
    if keywords is None:
        return _DEFAULT_KEYWORDS
    cleaned = tuple(k.strip() for k in keywords if k and k.strip())
    return cleaned or _DEFAULT_KEYWORDS


def _match_keywords(text: str, keywords: Sequence[str]) -> tuple[str, ...]:
    haystack = text.lower()
    return tuple(k for k in keywords if k.lower() in haystack)


async def regulatory_events_asof(
    ch: AsyncClient,
    *,
    asked_at: datetime,
    lookback_hours: int = 24 * 30,
    limit: int = 200,
    keywords: Sequence[str] | None = None,
    sources: list[str] | None = None,
) -> list[RegulatoryMatch]:
    """Return news/social events whose title+body match a regulatory keyword.

    Two passes:

    1. ``list_external_events`` over ``news``/``social`` event kinds so we
       only look at headline-shaped rows (macro/onchain rows are excluded
       by their own decay path and would be noise here).
    2. :func:`filter_regulatory_news` selects matching items; for each match
       we also re-derive the keyword list so the dashboard can highlight
       the specific keyword that hit.
    """
    if lookback_hours <= 0:
        raise ValueError("lookback_hours must be positive")
    keyword_set = _normalize_keywords(keywords)
    candidate_kinds = ["news", "social"]
    events = await list_external_events(
        ch,
        asked_at=asked_at,
        lookback_hours=lookback_hours,
        limit=limit,
        event_kinds=candidate_kinds,
        sources=sources,
    )
    if not events:
        return []

    # Build the (event, text) pair list so filter_regulatory_news preserves
    # the order we expect, then walk it again to attach matched keywords.
    texts = [
        f"{event.title or ''} {event.body or ''}" for event in events
    ]
    matched_texts = set(filter_regulatory_news(texts, keywords=keyword_set))
    matches: list[RegulatoryMatch] = []
    for event, text in zip(events, texts, strict=True):
        if text not in matched_texts:
            continue
        matches.append(
            RegulatoryMatch(
                event=event,
                matched_keywords=_match_keywords(text, keyword_set),
            )
        )
    return matches


__all__ = [
    "RegulatoryMatch",
    "regulatory_events_asof",
]
