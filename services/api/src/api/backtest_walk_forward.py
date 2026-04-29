"""Executable walk-forward backtest driver (M6.1).

This is the missing I/O layer on top of ``model.walk_forward_evaluate``:

1. Load resolved binary markets from ClickHouse.
2. Replay the served model point-in-time at one or more pre-resolution
   horizons via ``model_for_market(..., asked_at=...)``.
3. Score the resulting PIT samples with the pure evaluator in
   ``packages/model/src/model/backtest.py``.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from clickhouse_connect.driver.asyncclient import AsyncClient
from model import MarketType, WalkForwardReport, WalkForwardSample, walk_forward_evaluate

from . import asof as asof_q
from .clickhouse import get_async_client
from .markets import model_for_market
from .tuning import TuningProfile


@dataclass(frozen=True)
class HorizonSummary:
    horizon_hours: int
    scored_samples: int
    skipped_samples: int


@dataclass(frozen=True)
class ResolvedCorpusRow:
    condition_id: str
    resolved_outcome: str
    resolved_at: datetime
    resolution_observed_at: datetime
    dispute_status: str | None
    volume_usdc: float | None
    liquidity_usdc: float | None


@dataclass(frozen=True)
class CorpusSummary:
    total_resolved_markets: int
    binary_markets: int
    invalid_markets: int
    disputed_markets: int
    low_volume_markets: int
    missing_snapshot_markets: int


@dataclass(frozen=True)
class WalkForwardBacktestReport:
    horizons_hours: list[int]
    total_resolved_markets: int
    scored_samples: int
    skipped_samples: int
    by_horizon: list[HorizonSummary]
    corpus: CorpusSummary
    evaluation: WalkForwardReport


@dataclass(frozen=True)
class ReplaySampleRow:
    condition_id: str
    market_type: MarketType
    regime: str | None
    asked_at: datetime
    resolved_at: datetime
    predicted_prob: float
    outcome: int
    time_to_resolution_s: float
    band_lo: float | None
    band_hi: float | None

    def to_walk_forward_sample(self) -> WalkForwardSample:
        return WalkForwardSample(
            market_type=self.market_type,
            asked_at=self.asked_at.timestamp(),
            resolved_at=self.resolved_at.timestamp(),
            predicted_prob=self.predicted_prob,
            outcome=self.outcome,
            time_to_resolution_s=self.time_to_resolution_s,
            band_lo=self.band_lo,
            band_hi=self.band_hi,
            regime=self.regime,
        )


def _normalize_horizons(horizons_hours: tuple[int, ...] | list[int]) -> list[int]:
    normalized: list[int] = []
    for raw in horizons_hours:
        horizon = int(raw)
        if horizon <= 0 or horizon in normalized:
            continue
        normalized.append(horizon)
    if not normalized:
        raise ValueError("at least one positive horizon is required")
    return normalized


def _market_type_from_detail(raw: str) -> MarketType:
    try:
        return MarketType(raw)
    except ValueError:
        return MarketType.MISC


async def load_resolved_market_corpus(
    ch: AsyncClient,
    *,
    resolved_from: datetime | None = None,
    resolved_to: datetime | None = None,
    limit: int = 500,
    low_volume_threshold_usdc: float = 1_000.0,
) -> tuple[list[ResolvedCorpusRow], CorpusSummary]:
    conditions: list[str] = ["resolved_outcome IN ('YES', 'NO', 'INVALID')"]
    parameters: dict[str, object] = {"limit": limit}
    if resolved_from is not None:
        conditions.append("event_time >= {resolved_from:DateTime64(3)}")
        parameters["resolved_from"] = resolved_from
    if resolved_to is not None:
        conditions.append("event_time <= {resolved_to:DateTime64(3)}")
        parameters["resolved_to"] = resolved_to
    result = await ch.query(
        f"""
        SELECT condition_id, resolved_outcome, dispute_status, event_time, observed_at
        FROM (
            SELECT condition_id, resolved_outcome, dispute_status, event_time, observed_at
            FROM market_resolutions
            WHERE {' AND '.join(conditions)}
            ORDER BY condition_id, observed_at ASC
            LIMIT 1 BY condition_id
        )
        ORDER BY event_time DESC
        LIMIT {{limit:UInt32}}
        """,
        parameters=parameters,
    )
    rows: list[ResolvedCorpusRow] = []
    binary_markets = 0
    invalid_markets = 0
    disputed_markets = 0
    low_volume_markets = 0
    missing_snapshot_markets = 0
    for condition_id, resolved_outcome, dispute_status, resolved_at, resolution_observed_at in result.result_rows:
        snapshot = await asof_q.latest_market_snapshot_asof(
            ch,
            str(condition_id),
            resolution_observed_at,
        )
        volume_usdc = snapshot.volume_usdc if snapshot is not None else None
        liquidity_usdc = snapshot.liquidity_usdc if snapshot is not None else None
        if snapshot is None:
            missing_snapshot_markets += 1
        if resolved_outcome in {"YES", "NO"}:
            binary_markets += 1
        if resolved_outcome == "INVALID":
            invalid_markets += 1
        if str(dispute_status or "").strip():
            disputed_markets += 1
        if volume_usdc is not None and volume_usdc < low_volume_threshold_usdc:
            low_volume_markets += 1
        rows.append(
            ResolvedCorpusRow(
                condition_id=str(condition_id),
                resolved_outcome=str(resolved_outcome),
                resolved_at=resolved_at,
                resolution_observed_at=resolution_observed_at,
                dispute_status=str(dispute_status).strip() or None,
                volume_usdc=volume_usdc,
                liquidity_usdc=liquidity_usdc,
            )
        )
    return rows, CorpusSummary(
        total_resolved_markets=len(rows),
        binary_markets=binary_markets,
        invalid_markets=invalid_markets,
        disputed_markets=disputed_markets,
        low_volume_markets=low_volume_markets,
        missing_snapshot_markets=missing_snapshot_markets,
    )


async def replay_walk_forward_samples(
    ch: AsyncClient,
    *,
    horizons_hours: tuple[int, ...] | list[int] = (24, 72, 168),
    resolved_from: datetime | None = None,
    resolved_to: datetime | None = None,
    limit: int = 500,
    low_volume_threshold_usdc: float = 1_000.0,
    tuning_profile: TuningProfile | None = None,
) -> WalkForwardBacktestReport:
    horizons = _normalize_horizons(horizons_hours)
    corpus_rows, corpus_summary = await load_resolved_market_corpus(
        ch,
        resolved_from=resolved_from,
        resolved_to=resolved_to,
        limit=limit,
        low_volume_threshold_usdc=low_volume_threshold_usdc,
    )
    samples: list[ReplaySampleRow] = []
    horizon_stats = {
        horizon: HorizonSummary(
            horizon_hours=horizon,
            scored_samples=0,
            skipped_samples=0,
        )
        for horizon in horizons
    }

    for corpus_row in corpus_rows:
        if corpus_row.resolved_outcome not in {"YES", "NO"}:
            continue
        condition_id = corpus_row.condition_id
        resolved_outcome = corpus_row.resolved_outcome
        resolved_at = corpus_row.resolved_at
        for horizon in horizons:
            asked_at = resolved_at - timedelta(hours=horizon)
            detail = await model_for_market(
                ch,
                condition_id=condition_id,
                asked_at=asked_at,
                tuning_profile=tuning_profile,
            )
            if detail is None or detail.model_prob is None:
                prior = horizon_stats[horizon]
                horizon_stats[horizon] = HorizonSummary(
                    horizon_hours=horizon,
                    scored_samples=prior.scored_samples,
                    skipped_samples=prior.skipped_samples + 1,
                )
                continue
            samples.append(
                ReplaySampleRow(
                    condition_id=condition_id,
                    market_type=_market_type_from_detail(detail.market_type),
                    regime=detail.regime_label,
                    asked_at=asked_at,
                    resolved_at=resolved_at,
                    predicted_prob=float(detail.model_prob),
                    outcome=1 if resolved_outcome == "YES" else 0,
                    time_to_resolution_s=(resolved_at - asked_at).total_seconds(),
                    band_lo=detail.band_lo,
                    band_hi=detail.band_hi,
                )
            )
            prior = horizon_stats[horizon]
            horizon_stats[horizon] = HorizonSummary(
                horizon_hours=horizon,
                scored_samples=prior.scored_samples + 1,
                skipped_samples=prior.skipped_samples,
            )
    return samples, corpus_summary, [horizon_stats[horizon] for horizon in horizons]


async def run_walk_forward_backtest(
    ch: AsyncClient,
    *,
    horizons_hours: tuple[int, ...] | list[int] = (24, 72, 168),
    resolved_from: datetime | None = None,
    resolved_to: datetime | None = None,
    limit: int = 500,
    low_volume_threshold_usdc: float = 1_000.0,
    tuning_profile: TuningProfile | None = None,
) -> WalkForwardBacktestReport:
    replay_rows, corpus_summary, by_horizon = await replay_walk_forward_samples(
        ch,
        horizons_hours=horizons_hours,
        resolved_from=resolved_from,
        resolved_to=resolved_to,
        limit=limit,
        low_volume_threshold_usdc=low_volume_threshold_usdc,
        tuning_profile=tuning_profile,
    )
    evaluation = walk_forward_evaluate(
        [row.to_walk_forward_sample() for row in replay_rows]
    )
    horizons = _normalize_horizons(horizons_hours)
    return WalkForwardBacktestReport(
        horizons_hours=horizons,
        total_resolved_markets=corpus_summary.total_resolved_markets,
        scored_samples=sum(item.scored_samples for item in by_horizon),
        skipped_samples=sum(item.skipped_samples for item in by_horizon),
        by_horizon=by_horizon,
        corpus=corpus_summary,
        evaluation=evaluation,
    )


def _round_floats(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, list):
        return [_round_floats(item) for item in value]
    if isinstance(value, dict):
        return {key: _round_floats(item) for key, item in value.items()}
    return value


def _report_to_json(report: WalkForwardBacktestReport) -> str:
    return json.dumps(_round_floats(asdict(report)), indent=2)


async def _main() -> None:
    ch = await get_async_client()
    try:
        now = datetime.now(tz=UTC)
        report = await run_walk_forward_backtest(
            ch,
            horizons_hours=(24, 72, 168),
            resolved_from=now - timedelta(days=90),
            resolved_to=now,
            limit=500,
        )
        print(_report_to_json(report))
    finally:
        await ch.close()


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
