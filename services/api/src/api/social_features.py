"""Per-market structured social features (M4.1 §6.3).

Wires the existing structured-sentiment helpers in
:mod:`model.structured_sentiment` over the social rows already landing in
``external_events`` from RSS / Reddit ingestion (and X/Twitter once the
M4.1 source decision is made). The output is a deterministic snapshot
that the ensemble can consume as feature inputs without any LLM call.

What gets computed
------------------

* ``post_count_24h`` — number of social posts referencing the market in
  the trailing 24h window.
* ``reach_volume_24h`` — sum of impression-equivalent values across
  those posts. Reddit rows use the upvote/score field as a reach proxy;
  empty when the upstream doesn't expose impressions.
* ``novelty_score_latest`` — Jaccard-novelty of the most-recent post vs
  the trailing 24h history.
* ``sentiment_dispersion_24h`` — population stdev of lexicon scores
  across all 24h posts. Higher = disagreement, lower = consensus.
* ``tone_shift_24h_vs_7d`` — current-window mean lexicon sentiment
  minus the trailing-7d-baseline mean.

Sentiment is derived from :func:`model.score_text`'s deterministic
lexicon so the feature is fully reproducible and PIT-correct.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from clickhouse_connect.driver.asyncclient import AsyncClient
from model import (
    novelty_score,
    reach_adjusted_volume,
    score_text,
    sentiment_dispersion,
    tone_shift,
)


@dataclass(frozen=True)
class SocialPost:
    source: str
    title: str
    body: str
    event_time: datetime
    metadata: dict[str, Any]


@dataclass(frozen=True)
class SocialFeatures:
    asked_at: datetime
    condition_id: str
    post_count_24h: int
    post_count_7d: int
    reach_volume_24h: float
    novelty_score_latest: float
    sentiment_dispersion_24h: float
    tone_shift_24h_vs_7d: float


_DEFAULT_24H_WINDOW = timedelta(hours=24)
_DEFAULT_7D_WINDOW = timedelta(days=7)


def _post_text(post: SocialPost) -> str:
    return " ".join(filter(None, [post.title, post.body]))


def _post_reach(post: SocialPost) -> float:
    """Return an impression proxy.

    Reddit rows store upvote/score in metadata['score'] so we use that.
    When metadata doesn't carry a numeric reach signal, fall back to
    1.0 per post so volume still increments by post count.
    """
    score = post.metadata.get("score")
    if isinstance(score, (int, float)):
        return float(score)
    impressions = post.metadata.get("impressions")
    if isinstance(impressions, (int, float)):
        return float(impressions)
    return 1.0


def _parse_metadata(raw: object) -> dict[str, Any]:
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def _load_social_posts(
    ch: AsyncClient,
    *,
    condition_id: str,
    window_start: datetime,
    asked_at: datetime,
) -> list[SocialPost]:
    query = """
        SELECT source, title, body, metadata, event_time
          FROM external_events
         WHERE event_kind = 'social'
           AND has(related_markets, {cond:String})
           AND event_time >= {start:DateTime64(3)}
           AND observed_at <= {asof:DateTime64(3)}
         ORDER BY event_time ASC
    """
    result = await ch.query(
        query,
        parameters={
            "cond": condition_id,
            "start": window_start,
            "asof": asked_at,
        },
    )
    return [
        SocialPost(
            source=str(row[0]),
            title=str(row[1] or ""),
            body=str(row[2] or ""),
            event_time=row[4],
            metadata=_parse_metadata(row[3]),
        )
        for row in result.result_rows
    ]


async def _load_social_posts_batch(
    ch: AsyncClient,
    *,
    condition_ids: list[str],
    window_start: datetime,
    asked_at: datetime,
) -> dict[str, list[SocialPost]]:
    """Load social posts for many markets in one query.

    Posts are bucketed per ``condition_id`` they're related to. A single
    post can land in multiple buckets when ``related_markets`` references
    several markets (the RSS/Reddit lexical-overlap matcher emits up to
    ``max_related_markets`` per row).
    """
    if not condition_ids:
        return {}
    query = """
        SELECT source, title, body, metadata, event_time, related_markets
          FROM external_events
         WHERE event_kind = 'social'
           AND hasAny(related_markets, {conds:Array(String)})
           AND event_time >= {start:DateTime64(3)}
           AND observed_at <= {asof:DateTime64(3)}
         ORDER BY event_time ASC
    """
    result = await ch.query(
        query,
        parameters={
            "conds": list(condition_ids),
            "start": window_start,
            "asof": asked_at,
        },
    )
    target = set(condition_ids)
    out: dict[str, list[SocialPost]] = {cid: [] for cid in condition_ids}
    for row in result.result_rows:
        post = SocialPost(
            source=str(row[0]),
            title=str(row[1] or ""),
            body=str(row[2] or ""),
            event_time=row[4],
            metadata=_parse_metadata(row[3]),
        )
        for cid in row[5] or []:
            cid_str = str(cid)
            if cid_str in target:
                out[cid_str].append(post)
    return out


def _empty_features(*, asked_at: datetime, condition_id: str) -> SocialFeatures:
    return SocialFeatures(
        asked_at=asked_at,
        condition_id=condition_id,
        post_count_24h=0,
        post_count_7d=0,
        reach_volume_24h=0.0,
        novelty_score_latest=0.0,
        sentiment_dispersion_24h=0.0,
        tone_shift_24h_vs_7d=0.0,
    )


def compute_social_features(
    *,
    asked_at: datetime,
    condition_id: str,
    posts_7d: list[SocialPost],
    window_24h: timedelta = _DEFAULT_24H_WINDOW,
) -> SocialFeatures:
    """Pure transformation from a list of posts to the feature snapshot.

    Splits ``posts_7d`` (already filtered to the trailing 7d) into the
    24h sub-window for the live features, and uses the older 6d as the
    tone-shift baseline. With no posts at all returns the default
    snapshot so the ensemble can consume it without conditional logic.
    """
    if not posts_7d:
        return _empty_features(asked_at=asked_at, condition_id=condition_id)

    cutoff_24h = asked_at - window_24h
    posts_24h = [post for post in posts_7d if post.event_time >= cutoff_24h]
    posts_baseline = [post for post in posts_7d if post.event_time < cutoff_24h]

    sentiments_24h = [score_text(_post_text(post)) for post in posts_24h]
    sentiments_baseline = [
        score_text(_post_text(post)) for post in posts_baseline
    ]

    if posts_24h:
        latest = posts_24h[-1]
        history = [
            _post_text(post)
            for post in posts_24h[:-1]
        ]
        novelty = novelty_score(history, _post_text(latest))
        reach = reach_adjusted_volume(
            (_post_text(post), int(_post_reach(post))) for post in posts_24h
        )
    else:
        novelty = 0.0
        reach = 0.0

    dispersion = sentiment_dispersion(sentiments_24h)
    current_mean = (
        statistics.fmean(sentiments_24h) if sentiments_24h else 0.0
    )
    baseline_mean = (
        statistics.fmean(sentiments_baseline) if sentiments_baseline else 0.0
    )
    shift = tone_shift(current_mean, baseline_mean)

    return SocialFeatures(
        asked_at=asked_at,
        condition_id=condition_id,
        post_count_24h=len(posts_24h),
        post_count_7d=len(posts_7d),
        reach_volume_24h=float(reach),
        novelty_score_latest=float(novelty),
        sentiment_dispersion_24h=float(dispersion),
        tone_shift_24h_vs_7d=float(shift),
    )


async def social_features_for_market(
    ch: AsyncClient,
    *,
    condition_id: str,
    asked_at: datetime,
    window_24h: timedelta = _DEFAULT_24H_WINDOW,
    window_7d: timedelta = _DEFAULT_7D_WINDOW,
) -> SocialFeatures:
    """Load social posts and compute the feature snapshot."""
    posts = await _load_social_posts(
        ch,
        condition_id=condition_id,
        window_start=asked_at - window_7d,
        asked_at=asked_at,
    )
    return compute_social_features(
        asked_at=asked_at,
        condition_id=condition_id,
        posts_7d=posts,
        window_24h=window_24h,
    )


async def social_features_batch_asof(
    ch: AsyncClient,
    *,
    condition_ids: list[str],
    asked_at: datetime,
    window_24h: timedelta = _DEFAULT_24H_WINDOW,
    window_7d: timedelta = _DEFAULT_7D_WINDOW,
) -> dict[str, SocialFeatures]:
    """Compute social features for many markets in one ClickHouse query.

    The list path renders ~200 markets per request; fanning out one query
    per market would 200x the read amplification on ``external_events``.
    This batched helper does a single ``hasAny`` query then buckets posts
    per condition_id in Python before computing each market's snapshot.
    Markets with no matching posts get an empty :class:`SocialFeatures`.
    """
    if not condition_ids:
        return {}
    posts_by_cid = await _load_social_posts_batch(
        ch,
        condition_ids=condition_ids,
        window_start=asked_at - window_7d,
        asked_at=asked_at,
    )
    return {
        cid: compute_social_features(
            asked_at=asked_at,
            condition_id=cid,
            posts_7d=posts_by_cid.get(cid, []),
            window_24h=window_24h,
        )
        for cid in condition_ids
    }


__all__ = [
    "SocialFeatures",
    "SocialPost",
    "compute_social_features",
    "social_features_batch_asof",
    "social_features_for_market",
]
