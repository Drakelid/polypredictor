"""Resolved-analog priors for long-tail binary markets.

This is the M1 cold-start path for long-tail binaries. We derive two priors
from the point-in-time store:

  * ``historical_base_rate``: a smoothed YES frequency over the nearest
    relevant resolved pool (same event family if available, otherwise same
    asset, otherwise all long-tail resolved markets).
  * ``embedding_prior``: a lightweight cosine prior over tokenized market text
    against resolved analogs, weighted by similarity.

The similarity model is intentionally simple for M1: token-space cosine rather
than a dedicated embedding service. It is still PIT-correct and slots cleanly
into the baseline interface.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from clickhouse_connect.driver.asyncclient import AsyncClient
from model import ClassificationResult


@dataclass(frozen=True)
class LongTailResolvedMarket:
    condition_id: str
    question: str
    slug: str
    tags: list[str]
    outcome: int
    asset: str | None
    event_family: str | None


@dataclass(frozen=True)
class LongTailPriorContext:
    embedding_prior: float | None
    historical_base_rate: float | None


async def load_long_tail_resolved_markets(
    ch: AsyncClient,
    *,
    asked_at: object,
    limit: int = 2000,
) -> list[LongTailResolvedMarket]:
    query = """
        SELECT res.condition_id, res.resolved_outcome, snap.question, snap.slug,
               snap.tags, cls.asset, cls.event_family
        FROM (
            SELECT condition_id, resolved_outcome, event_time, observed_at
            FROM market_resolutions
            WHERE resolved_outcome IN ('YES', 'NO')
              AND observed_at <= {asof:DateTime64(3)}
            ORDER BY condition_id, observed_at DESC
            LIMIT 1 BY condition_id
        ) AS res
        INNER JOIN (
            SELECT condition_id, question, slug, tags, observed_at
            FROM markets_snapshots
            WHERE observed_at <= {asof:DateTime64(3)}
            ORDER BY condition_id, observed_at DESC
            LIMIT 1 BY condition_id
        ) AS snap USING (condition_id)
        INNER JOIN (
            SELECT condition_id, market_type, asset, event_family, observed_at
            FROM market_classifications
            WHERE observed_at <= {asof:DateTime64(3)}
            ORDER BY condition_id, observed_at DESC
            LIMIT 1 BY condition_id
        ) AS cls USING (condition_id)
        WHERE cls.market_type = 'long_tail_binary'
        ORDER BY res.event_time DESC
        LIMIT {limit:UInt32}
    """
    result = await ch.query(query, parameters={"asof": asked_at, "limit": limit})
    out: list[LongTailResolvedMarket] = []
    for row in result.result_rows:
        outcome = 1 if str(row[1]) == "YES" else 0
        out.append(
            LongTailResolvedMarket(
                condition_id=str(row[0]),
                question=str(row[2]),
                slug=str(row[3]),
                tags=list(row[4]) if row[4] else [],
                outcome=outcome,
                asset=str(row[5]) if row[5] else None,
                event_family=str(row[6]) if row[6] else None,
            )
        )
    return out


def long_tail_prior_context(
    *,
    current_condition_id: str,
    question: str,
    slug: str,
    tags: list[str],
    classification: ClassificationResult,
    candidates: list[LongTailResolvedMarket],
) -> LongTailPriorContext:
    others = [
        candidate
        for candidate in candidates
        if candidate.condition_id != current_condition_id
    ]
    if not others:
        return LongTailPriorContext(embedding_prior=None, historical_base_rate=None)

    base_rate = _historical_base_rate(
        classification=classification,
        candidates=others,
    )
    embedding_prior = _embedding_prior(
        question=question,
        slug=slug,
        tags=tags,
        classification=classification,
        candidates=others,
    )
    return LongTailPriorContext(
        embedding_prior=embedding_prior,
        historical_base_rate=base_rate,
    )


def _historical_base_rate(
    *,
    classification: ClassificationResult,
    candidates: list[LongTailResolvedMarket],
) -> float | None:
    family = classification.features.event_family
    asset = classification.features.asset

    pool = [candidate for candidate in candidates if candidate.event_family == family] if family else []
    if len(pool) < 3 and asset is not None:
        pool = [candidate for candidate in candidates if candidate.asset == asset]
    if len(pool) < 3:
        pool = list(candidates)
    if len(pool) < 3:
        return None

    yeses = sum(candidate.outcome for candidate in pool)
    # Laplace smoothing keeps the prior away from exact 0/1 on tiny samples.
    return (yeses + 1.0) / (len(pool) + 2.0)


def _embedding_prior(
    *,
    question: str,
    slug: str,
    tags: list[str],
    classification: ClassificationResult,
    candidates: list[LongTailResolvedMarket],
) -> float | None:
    target = _text_vector(
        question=question,
        slug=slug,
        tags=tags,
        asset=classification.features.asset,
        event_family=classification.features.event_family,
    )
    if not target:
        return None

    scored: list[tuple[float, LongTailResolvedMarket]] = []
    for candidate in candidates:
        vector = _text_vector(
            question=candidate.question,
            slug=candidate.slug,
            tags=candidate.tags,
            asset=candidate.asset,
            event_family=candidate.event_family,
        )
        similarity = _cosine_similarity(target, vector)
        if similarity >= 0.15:
            scored.append((similarity, candidate))

    if not scored:
        return None

    scored.sort(key=lambda item: item[0], reverse=True)
    top = scored[:5]
    weight_total = sum(weight for weight, _ in top)
    if weight_total <= 0:
        return None
    return sum(weight * candidate.outcome for weight, candidate in top) / weight_total


def _text_vector(
    *,
    question: str,
    slug: str,
    tags: list[str],
    asset: str | None,
    event_family: str | None,
) -> Counter[str]:
    text_parts = [
        question,
        slug.replace("-", " "),
        " ".join(tags),
        asset or "",
        event_family or "",
    ]
    tokens = [token for token in _tokenize(" ".join(text_parts)) if token not in _STOPWORDS]
    return Counter(tokens)


def _tokenize(text: str) -> Iterable[str]:
    for token in re.findall(r"[a-z0-9]+", text.lower()):
        if len(token) <= 1:
            continue
        yield token


def _cosine_similarity(left: Counter[str], right: Counter[str]) -> float:
    if not left or not right:
        return 0.0
    shared = set(left) & set(right)
    numerator = sum(left[token] * right[token] for token in shared)
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    return numerator / (left_norm * right_norm)


_STOPWORDS = {
    "a",
    "an",
    "and",
    "at",
    "be",
    "by",
    "for",
    "in",
    "is",
    "of",
    "on",
    "or",
    "the",
    "to",
    "will",
}
