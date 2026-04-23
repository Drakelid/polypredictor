"""As-of reads over ``market_classifications`` (PRD §6.1).

Classifications are append-only; re-labels are new rows. The latest row with
``observed_at <= asked_at`` is the label the model would have seen at that
time. This lets backtests replay historical features without leaking future
re-labels into the past.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from clickhouse_connect.driver.asyncclient import AsyncClient
from model import MarketFeatures, MarketType


@dataclass(frozen=True)
class ClassificationRow:
    condition_id: str
    market_type: MarketType
    confidence: float
    needs_review: bool
    reviewer_flag: str
    classifier: str
    features: MarketFeatures
    reasons: list[str]
    event_time: datetime
    observed_at: datetime


def _row_to_classification(r: tuple) -> ClassificationRow:
    (
        condition_id,
        market_type,
        confidence,
        needs_review,
        reviewer_flag,
        classifier,
        asset,
        strike,
        range_low,
        range_high,
        direction,
        event_family,
        resolution_date,
        reasons,
        event_time,
        observed_at,
    ) = r
    features = MarketFeatures(
        asset=asset or None,
        strike=float(strike) if strike is not None else None,
        range_low=float(range_low) if range_low is not None else None,
        range_high=float(range_high) if range_high is not None else None,
        direction=direction or None,
        event_family=event_family or None,
        resolution_date=resolution_date,
        is_multi_outcome_sibling=MarketType(market_type) is MarketType.MULTI_OUTCOME,
    )
    return ClassificationRow(
        condition_id=str(condition_id),
        market_type=MarketType(str(market_type)),
        confidence=float(confidence),
        needs_review=bool(needs_review),
        reviewer_flag=str(reviewer_flag or ""),
        classifier=str(classifier or ""),
        features=features,
        reasons=[s for s in str(reasons or "").split("|") if s],
        event_time=event_time,
        observed_at=observed_at,
    )


_COLUMNS = (
    "condition_id, market_type, confidence, needs_review, reviewer_flag, classifier, "
    "asset, strike, range_low, range_high, direction, event_family, resolution_date, "
    "reasons, event_time, observed_at"
)


async def classification_asof(
    ch: AsyncClient, condition_id: str, asked_at: datetime
) -> ClassificationRow | None:
    """Latest classification row for ``condition_id`` known at ``asked_at``."""
    query = f"""
        SELECT {_COLUMNS}
        FROM market_classifications
        WHERE condition_id = {{cond:String}}
          AND observed_at <= {{asof:DateTime64(3)}}
        ORDER BY observed_at DESC
        LIMIT 1
    """
    result = await ch.query(query, parameters={"cond": condition_id, "asof": asked_at})
    rows = result.result_rows
    if not rows:
        return None
    return _row_to_classification(rows[0])


async def classifications_batch_asof(
    ch: AsyncClient, condition_ids: list[str], asked_at: datetime
) -> dict[str, ClassificationRow]:
    """Batch variant — one round-trip for a whole page of market IDs."""
    if not condition_ids:
        return {}
    query = f"""
        SELECT {_COLUMNS}
        FROM market_classifications
        WHERE condition_id IN {{cids:Array(String)}}
          AND observed_at <= {{asof:DateTime64(3)}}
        ORDER BY condition_id, observed_at DESC
        LIMIT 1 BY condition_id
    """
    result = await ch.query(
        query, parameters={"cids": list(condition_ids), "asof": asked_at}
    )
    out: dict[str, ClassificationRow] = {}
    for r in result.result_rows:
        row = _row_to_classification(r)
        out[row.condition_id] = row
    return out
