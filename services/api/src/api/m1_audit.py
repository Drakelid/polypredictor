"""Executable M1 exit-criteria audit helpers.

This module keeps the remaining M1 checks concrete:

* active-market type coverage can be measured directly against the PIT store
* threshold baselines can dump the live Deribit inputs used for spot checks

It intentionally does not mark exit criteria as passed on its own; it only
produces the evidence needed to evaluate them on a live dataset.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from clickhouse_connect.driver.asyncclient import AsyncClient
from model import DeribitClient, DeribitIV, MarketType, classify

from .classifications import ClassificationRow, classifications_batch_asof
from .clickhouse import get_async_client
from .markets import (
    _deribit_currency,
    _horizon_days,
    _latest_markets,
    _latest_mids_for_tokens,
    _multi_outcome_contexts_for_markets,
)
from .settings import get_settings


@dataclass(frozen=True)
class ActiveTypeCoverage:
    total_active: int
    non_misc_active: int
    non_misc_ratio: float | None
    needs_review_active: int
    by_type: dict[str, int]


@dataclass(frozen=True)
class ThresholdSpotCheck:
    condition_id: str
    question: str
    slug: str
    market_mid: float | None
    confidence: float
    needs_review: bool
    asset: str | None
    strike: float | None
    direction: str | None
    resolution_date: datetime | None
    horizon_days: float | None
    status: str
    index_price: float | None
    realized_vol: float | None
    atm_iv: float | None
    strike_iv: float | None
    atm_instrument: str | None
    strike_instrument: str | None


@dataclass(frozen=True)
class M1AuditReport:
    as_of: datetime
    active_limit: int
    threshold_sample_limit: int
    coverage: ActiveTypeCoverage
    threshold_spot_checks: list[ThresholdSpotCheck]


def _effective_classification(
    snap: dict[str, object],
    classification: ClassificationRow | None,
    *,
    sibling_count: int,
    asked_at: datetime,
) -> ClassificationRow:
    if classification is not None:
        return classification
    result = classify(
        question=str(snap["question"]),
        description=None,
        tags=list(snap.get("tags") or []),
        end_date=snap.get("end_date"),  # type: ignore[arg-type]
        multi_outcome_sibling_count=sibling_count,
    )
    return ClassificationRow(
        condition_id=str(snap["condition_id"]),
        market_type=result.market_type,
        confidence=result.confidence,
        needs_review=result.needs_review,
        reviewer_flag="pending" if result.needs_review else "",
        classifier="regex_v1",
        features=result.features,
        reasons=result.reasons,
        event_time=asked_at,
        observed_at=asked_at,
    )


async def _active_market_context(
    ch: AsyncClient,
    *,
    asked_at: datetime,
    limit: int,
) -> tuple[list[dict[str, object]], dict[str, ClassificationRow], dict[str, int], dict[str, float]]:
    snaps = await _latest_markets(ch, asked_at, limit=limit)
    classifications = await classifications_batch_asof(
        ch, [str(snap["condition_id"]) for snap in snaps], asked_at
    )
    multi_outcome_contexts = await _multi_outcome_contexts_for_markets(ch, snaps, asked_at)
    sibling_counts = {
        condition_id: len(context.raw)
        for condition_id, context in multi_outcome_contexts.items()
    }
    all_tokens: list[str] = []
    for snap in snaps:
        all_tokens.extend(list(snap.get("token_ids") or []))  # type: ignore[arg-type]
    mids = await _latest_mids_for_tokens(ch, all_tokens, asked_at)
    return snaps, classifications, sibling_counts, mids


async def active_type_coverage(
    ch: AsyncClient,
    *,
    asked_at: datetime,
    limit: int = 5000,
) -> ActiveTypeCoverage:
    snaps, classifications, sibling_counts, _ = await _active_market_context(
        ch, asked_at=asked_at, limit=limit
    )
    counts: Counter[str] = Counter()
    non_misc = 0
    needs_review = 0
    for snap in snaps:
        condition_id = str(snap["condition_id"])
        classification = _effective_classification(
            snap,
            classifications.get(condition_id),
            sibling_count=sibling_counts.get(condition_id, 0),
            asked_at=asked_at,
        )
        counts[classification.market_type.value] += 1
        if classification.market_type is not MarketType.MISC:
            non_misc += 1
        if classification.needs_review:
            needs_review += 1
    total = len(snaps)
    return ActiveTypeCoverage(
        total_active=total,
        non_misc_active=non_misc,
        non_misc_ratio=(non_misc / total) if total else None,
        needs_review_active=needs_review,
        by_type=dict(sorted(counts.items())),
    )


async def sample_threshold_inputs(
    ch: AsyncClient,
    *,
    asked_at: datetime,
    active_limit: int = 5000,
    sample_limit: int = 10,
    deribit: DeribitClient | None = None,
) -> list[ThresholdSpotCheck]:
    snaps, classifications, sibling_counts, mids = await _active_market_context(
        ch, asked_at=asked_at, limit=active_limit
    )
    own_deribit = deribit is None
    if deribit is None:
        settings = get_settings()
        deribit = DeribitClient(base_url=settings.deribit_base)
    try:
        checks: list[ThresholdSpotCheck] = []
        for snap in snaps:
            condition_id = str(snap["condition_id"])
            classification = _effective_classification(
                snap,
                classifications.get(condition_id),
                sibling_count=sibling_counts.get(condition_id, 0),
                asked_at=asked_at,
            )
            if classification.market_type is not MarketType.THRESHOLD:
                continue
            token_ids = list(snap.get("token_ids") or [])  # type: ignore[arg-type]
            yes_mid = mids.get(str(token_ids[0])) if token_ids else None
            iv = await _threshold_iv_for_classification(
                deribit,
                classification=classification,
                asked_at=asked_at,
            )
            checks.append(
                _spot_check_from_iv(
                    snap,
                    classification=classification,
                    market_mid=yes_mid,
                    asked_at=asked_at,
                    iv=iv,
                )
            )
            if len(checks) >= sample_limit:
                break
        return checks
    finally:
        if own_deribit:
            await deribit.aclose()


async def _threshold_iv_for_classification(
    deribit: DeribitClient,
    *,
    classification: ClassificationRow,
    asked_at: datetime,
) -> DeribitIV | None:
    currency = _deribit_currency(classification.features.asset)
    horizon_days = _horizon_days(asked_at, classification.features.resolution_date)
    strike = classification.features.strike
    direction = classification.features.direction
    if (
        currency is None
        or horizon_days is None
        or horizon_days <= 0
        or strike is None
        or direction is None
    ):
        return None
    return await deribit.fetch_iv(
        currency=currency,
        strike=float(strike),
        direction=direction,
        horizon_days=horizon_days,
    )


def _spot_check_from_iv(
    snap: dict[str, object],
    *,
    classification: ClassificationRow,
    market_mid: float | None,
    asked_at: datetime,
    iv: DeribitIV | None,
) -> ThresholdSpotCheck:
    currency = _deribit_currency(classification.features.asset)
    horizon_days = _horizon_days(asked_at, classification.features.resolution_date)
    status = "ok"
    if currency is None:
        status = "unsupported_asset"
    elif classification.features.strike is None or classification.features.direction is None:
        status = "missing_threshold_features"
    elif horizon_days is None or horizon_days <= 0:
        status = "missing_or_past_resolution_date"
    elif iv is None:
        status = "no_deribit_iv"
    return ThresholdSpotCheck(
        condition_id=str(snap["condition_id"]),
        question=str(snap["question"]),
        slug=str(snap.get("slug") or ""),
        market_mid=market_mid,
        confidence=classification.confidence,
        needs_review=classification.needs_review,
        asset=classification.features.asset,
        strike=classification.features.strike,
        direction=classification.features.direction,
        resolution_date=classification.features.resolution_date,
        horizon_days=horizon_days,
        status=status,
        index_price=iv.index_price if iv is not None else None,
        realized_vol=iv.realized_vol if iv is not None else None,
        atm_iv=iv.atm_iv if iv is not None else None,
        strike_iv=iv.strike_iv if iv is not None else None,
        atm_instrument=iv.atm_instrument if iv is not None else None,
        strike_instrument=iv.strike_instrument if iv is not None else None,
    )


async def run_m1_audit(
    ch: AsyncClient,
    *,
    asked_at: datetime | None = None,
    active_limit: int = 5000,
    threshold_sample_limit: int = 10,
) -> M1AuditReport:
    as_of = asked_at or datetime.now(tz=UTC)
    coverage = await active_type_coverage(ch, asked_at=as_of, limit=active_limit)
    threshold_spot_checks = await sample_threshold_inputs(
        ch,
        asked_at=as_of,
        active_limit=active_limit,
        sample_limit=threshold_sample_limit,
    )
    return M1AuditReport(
        as_of=as_of,
        active_limit=active_limit,
        threshold_sample_limit=threshold_sample_limit,
        coverage=coverage,
        threshold_spot_checks=threshold_spot_checks,
    )


def _report_to_json(report: M1AuditReport) -> str:
    payload = asdict(report)
    coverage = payload.get("coverage")
    if isinstance(coverage, dict):
        ratio = coverage.get("non_misc_ratio")
        if isinstance(ratio, float):
            coverage["non_misc_ratio"] = round(ratio, 6)
    for sample in payload.get("threshold_spot_checks", []):
        for key in ("market_mid", "confidence", "strike", "horizon_days", "index_price", "realized_vol", "atm_iv", "strike_iv"):
            value = sample.get(key)
            if isinstance(value, float):
                sample[key] = round(value, 6)
    return json.dumps(payload, indent=2, default=str)


async def _main() -> None:
    ch = await get_async_client()
    try:
        report = await run_m1_audit(ch)
        print(_report_to_json(report))
    finally:
        await ch.close()


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
