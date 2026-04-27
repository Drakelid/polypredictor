"""Rolling per-KOL credibility from KOL-tagged posts vs market outcomes.

This module turns the M4.1 ``Per-KOL credibility weighted by rolling edge
contribution`` task into a real computation: for each KOL-tagged social
post in the lookback window, score the message sentiment, look up the
related market's first resolution, and aggregate into a per-author
credibility weight with exponential time decay.

The resulting index — ``author_lower -> credibility_weight`` — plugs
directly into ``model.structured_sentiment.credibility_weight`` (which
already accepts an ``author_weights`` mapping). Authors with no resolved
posts default to ``1.0`` at the lookup site.

Today's social rows are Reddit-only; the same query shape and aggregation
work for X handles once §11.3 is decided. The shipping piece here is the
algorithm + I/O — wiring into the live ``social_features`` weighting path
is intentionally left for the same retraining cycle that picks up X data.
"""

from __future__ import annotations

import asyncio
import json
import math
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from clickhouse_connect.driver.asyncclient import AsyncClient
from model.sentiment_lexicon import score_text

from .clickhouse import get_async_client

DEFAULT_LOOKBACK_DAYS: int = 180
DEFAULT_HALF_LIFE_DAYS: float = 30.0
DEFAULT_WEIGHT_LO: float = 0.5
DEFAULT_WEIGHT_HI: float = 1.5
DEFAULT_NEUTRAL_WEIGHT: float = 1.0
DEFAULT_MIN_SAMPLES: int = 3


@dataclass(frozen=True)
class KolPost:
    author: str
    related_markets: tuple[str, ...]
    text: str
    event_time: datetime


@dataclass(frozen=True)
class KolEdgeContribution:
    author: str
    sample_count: int
    correct_count: int
    weighted_edge: float
    raw_hit_rate: float | None
    credibility_weight: float


async def load_kol_posts_asof(
    ch: AsyncClient,
    *,
    asked_at: datetime,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> list[KolPost]:
    """Pull KOL-tagged social posts in the trailing ``lookback_days`` window.

    The ``metadata.is_kol`` flag is set by
    :func:`ingest.workers.reddit_ingest.build_external_event_rows` when an
    author matches the configured KOL list, so the filter pushes down via
    ``JSONExtractBool``.
    """
    query = """
        SELECT author, title, body, related_markets, event_time
          FROM external_events
         WHERE event_kind = 'social'
           AND JSONExtractBool(metadata, 'is_kol') = 1
           AND event_time >= {start:DateTime64(3)}
           AND observed_at <= {asof:DateTime64(3)}
           AND length(related_markets) > 0
         ORDER BY event_time ASC
    """
    result = await ch.query(
        query,
        parameters={
            "start": asked_at - timedelta(days=lookback_days),
            "asof": asked_at,
        },
    )
    posts: list[KolPost] = []
    for author, title, body, related_markets, event_time in result.result_rows:
        author_str = str(author or "").strip()
        if not author_str:
            continue
        text = " ".join(filter(None, [str(title or ""), str(body or "")])).strip()
        if not text:
            continue
        cids = tuple(str(cid) for cid in (related_markets or []) if str(cid))
        if not cids:
            continue
        posts.append(
            KolPost(
                author=author_str,
                related_markets=cids,
                text=text,
                event_time=event_time,  # type: ignore[arg-type]
            )
        )
    return posts


async def load_first_resolutions(
    ch: AsyncClient,
    *,
    condition_ids: list[str],
    asked_at: datetime,
) -> dict[str, int]:
    """Map condition_id -> outcome (YES=1, NO=0) for *first* resolution rows.

    Uses ``LIMIT 1 BY condition_id ORDER BY observed_at ASC`` so revisions
    don't poison the credibility computation (M6.1 PIT contract).
    INVALID and missing markets are filtered out.
    """
    if not condition_ids:
        return {}
    result = await ch.query(
        """
        SELECT condition_id, resolved_outcome
          FROM market_resolutions
         WHERE condition_id IN {conds:Array(String)}
           AND observed_at <= {asof:DateTime64(3)}
           AND resolved_outcome IN ('YES', 'NO')
         ORDER BY condition_id, observed_at ASC
         LIMIT 1 BY condition_id
        """,
        parameters={
            "conds": list({str(cid) for cid in condition_ids}),
            "asof": asked_at,
        },
    )
    return {
        str(row[0]): 1 if str(row[1]) == "YES" else 0
        for row in result.result_rows
    }


def _decay(*, asked_at: datetime, event_time: datetime, half_life_days: float) -> float:
    if half_life_days <= 0:
        return 1.0
    delta_days = max(0.0, (asked_at - event_time).total_seconds() / 86_400.0)
    return math.pow(0.5, delta_days / half_life_days)


def _post_correct(
    *, sentiment: float, outcome: int
) -> int | None:
    """Returns 1 if the post correctly predicted, 0 if wrong, None if neutral."""
    if sentiment > 0:
        return 1 if outcome == 1 else 0
    if sentiment < 0:
        return 1 if outcome == 0 else 0
    return None


def aggregate_kol_credibility(
    posts: list[KolPost],
    resolutions: dict[str, int],
    *,
    asked_at: datetime,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
    weight_lo: float = DEFAULT_WEIGHT_LO,
    weight_hi: float = DEFAULT_WEIGHT_HI,
    neutral_weight: float = DEFAULT_NEUTRAL_WEIGHT,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> list[KolEdgeContribution]:
    """Pure rollup: posts + resolutions -> per-author credibility entries.

    For each (post, related-market) pair where the market is resolved and
    the post sentiment is non-zero, contribute ``+1`` for a correct call
    and ``-1`` for a wrong call. Each contribution is scaled by an
    exponential time decay anchored on ``asked_at``. The author's rolling
    edge is the decay-weighted mean of these +/-1 contributions, mapped
    linearly onto ``[weight_lo, weight_hi]`` with ``+1 -> weight_hi`` and
    ``-1 -> weight_lo``. Authors with fewer than ``min_samples`` resolved
    contributions get ``neutral_weight`` so a single lucky / unlucky call
    can't move the weight.
    """
    if weight_lo > weight_hi:
        raise ValueError("weight_lo must be <= weight_hi")
    by_author: dict[str, dict[str, float | int]] = defaultdict(
        lambda: {"weighted_edge_num": 0.0, "weighted_edge_den": 0.0, "correct": 0, "samples": 0}
    )
    for post in posts:
        sentiment = score_text(post.text)
        if sentiment == 0.0:
            continue
        # Case-fold the author key so e.g. "Alice" and "ALICE" collapse onto
        # a single rolling-edge bucket; matches ingest.kol_loader's lowercased
        # index so consumer lookups line up on both sides.
        author_key = post.author.strip().lower()
        if not author_key:
            continue
        decay = _decay(
            asked_at=asked_at,
            event_time=post.event_time,
            half_life_days=half_life_days,
        )
        for cid in post.related_markets:
            outcome = resolutions.get(cid)
            if outcome is None:
                continue
            correct = _post_correct(sentiment=sentiment, outcome=outcome)
            if correct is None:
                continue
            contribution = 1.0 if correct == 1 else -1.0
            entry = by_author[author_key]
            entry["weighted_edge_num"] = float(entry["weighted_edge_num"]) + decay * contribution
            entry["weighted_edge_den"] = float(entry["weighted_edge_den"]) + decay
            entry["correct"] = int(entry["correct"]) + correct
            entry["samples"] = int(entry["samples"]) + 1
    rows: list[KolEdgeContribution] = []
    half_range = (weight_hi - weight_lo) / 2.0
    midpoint = (weight_hi + weight_lo) / 2.0
    for author, entry in sorted(by_author.items()):
        samples = int(entry["samples"])
        correct = int(entry["correct"])
        denom = float(entry["weighted_edge_den"])
        weighted_edge = (
            float(entry["weighted_edge_num"]) / denom if denom > 0 else 0.0
        )
        if samples < min_samples or denom <= 0:
            credibility = neutral_weight
        else:
            credibility = midpoint + half_range * weighted_edge
            credibility = max(weight_lo, min(weight_hi, credibility))
        raw_hit_rate = (correct / samples) if samples > 0 else None
        rows.append(
            KolEdgeContribution(
                author=author,
                sample_count=samples,
                correct_count=correct,
                weighted_edge=weighted_edge,
                raw_hit_rate=raw_hit_rate,
                credibility_weight=credibility,
            )
        )
    return rows


def credibility_index_from_rows(
    rows: list[KolEdgeContribution],
) -> dict[str, float]:
    """Build a case-insensitive ``author_lower -> credibility_weight`` map.

    Mirrors the lowercasing convention used by ``ingest.kol_loader`` so
    consumer lookups match the same author key shape on both sides.
    """
    return {row.author.strip().lower(): row.credibility_weight for row in rows}


async def kol_credibility_index_asof(
    ch: AsyncClient,
    *,
    asked_at: datetime,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
    weight_lo: float = DEFAULT_WEIGHT_LO,
    weight_hi: float = DEFAULT_WEIGHT_HI,
    neutral_weight: float = DEFAULT_NEUTRAL_WEIGHT,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> dict[str, float]:
    """One-call helper that loads posts + resolutions and returns the index."""
    posts = await load_kol_posts_asof(
        ch, asked_at=asked_at, lookback_days=lookback_days
    )
    if not posts:
        return {}
    cids = sorted({cid for post in posts for cid in post.related_markets})
    resolutions = await load_first_resolutions(
        ch, condition_ids=cids, asked_at=asked_at
    )
    rows = aggregate_kol_credibility(
        posts,
        resolutions,
        asked_at=asked_at,
        half_life_days=half_life_days,
        weight_lo=weight_lo,
        weight_hi=weight_hi,
        neutral_weight=neutral_weight,
        min_samples=min_samples,
    )
    return credibility_index_from_rows(rows)


@dataclass(frozen=True)
class KolCredibilityReport:
    asked_at: datetime
    lookback_days: int
    half_life_days: float
    weight_lo: float
    weight_hi: float
    neutral_weight: float
    min_samples: int
    total_authors: int
    authors_with_min_samples: int
    rows: list[KolEdgeContribution]


async def run_kol_credibility(
    ch: AsyncClient,
    *,
    asked_at: datetime | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
    weight_lo: float = DEFAULT_WEIGHT_LO,
    weight_hi: float = DEFAULT_WEIGHT_HI,
    neutral_weight: float = DEFAULT_NEUTRAL_WEIGHT,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> KolCredibilityReport:
    as_of = asked_at or datetime.now(tz=UTC)
    posts = await load_kol_posts_asof(
        ch, asked_at=as_of, lookback_days=lookback_days
    )
    cids = sorted({cid for post in posts for cid in post.related_markets})
    resolutions = (
        await load_first_resolutions(ch, condition_ids=cids, asked_at=as_of)
        if cids
        else {}
    )
    rows = aggregate_kol_credibility(
        posts,
        resolutions,
        asked_at=as_of,
        half_life_days=half_life_days,
        weight_lo=weight_lo,
        weight_hi=weight_hi,
        neutral_weight=neutral_weight,
        min_samples=min_samples,
    )
    authors_with_min_samples = sum(
        1 for row in rows if row.sample_count >= min_samples
    )
    return KolCredibilityReport(
        asked_at=as_of,
        lookback_days=lookback_days,
        half_life_days=half_life_days,
        weight_lo=weight_lo,
        weight_hi=weight_hi,
        neutral_weight=neutral_weight,
        min_samples=min_samples,
        total_authors=len(rows),
        authors_with_min_samples=authors_with_min_samples,
        rows=rows,
    )


def _report_to_json(report: KolCredibilityReport) -> str:
    payload = asdict(report)
    for row in payload.get("rows", []):
        for key in ("weighted_edge", "raw_hit_rate", "credibility_weight"):
            value = row.get(key)
            if isinstance(value, float):
                row[key] = round(value, 6)
    return json.dumps(payload, indent=2, default=str)


async def _main() -> None:
    ch = await get_async_client()
    try:
        report = await run_kol_credibility(ch)
        print(_report_to_json(report))
    finally:
        await ch.close()


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
