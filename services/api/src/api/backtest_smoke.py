"""Baseline-vs-market smoke backtest for resolved markets.

M1 exit criterion: log baseline Brier score vs market-mid Brier score per
market type. This is intentionally lightweight: it replays a configurable
point-in-time snapshot before each market's resolution and aggregates the
resulting errors by type.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from statistics import mean

from clickhouse_connect.driver.asyncclient import AsyncClient

from .clickhouse import get_async_client
from .markets import model_for_market


@dataclass(frozen=True)
class BacktestSample:
    condition_id: str
    asked_at: datetime
    resolved_at: datetime
    outcome: int
    market_type: str
    model_prob: float
    market_mid: float
    baseline_source: str


@dataclass(frozen=True)
class BacktestTypeSummary:
    market_type: str
    samples: int
    baseline_brier: float
    market_mid_brier: float
    brier_skill_vs_mid: float | None


@dataclass(frozen=True)
class BacktestReport:
    horizon_hours: int
    total_resolved_markets: int
    scored_samples: int
    skipped_samples: int
    by_type: list[BacktestTypeSummary]


async def latest_binary_resolutions(
    ch: AsyncClient,
    *,
    resolved_from: datetime | None = None,
    resolved_to: datetime | None = None,
    limit: int = 500,
) -> list[tuple[str, str, datetime]]:
    """First-observed YES/NO resolution row per market in the requested window.

    Revisions remain in ``market_resolutions`` as later rows, but replay and
    backtest paths freeze the label at the original observed resolution so a
    later UMA dispute or manual correction does not rewrite history.
    """
    conditions: list[str] = [
        "resolved_outcome IN ('YES', 'NO')",
    ]
    parameters: dict[str, object] = {"limit": limit}
    if resolved_from is not None:
        conditions.append("event_time >= {resolved_from:DateTime64(3)}")
        parameters["resolved_from"] = resolved_from
    if resolved_to is not None:
        conditions.append("event_time <= {resolved_to:DateTime64(3)}")
        parameters["resolved_to"] = resolved_to
    where = " AND ".join(conditions)
    query = f"""
        SELECT condition_id, resolved_outcome, event_time
        FROM (
            SELECT condition_id, resolved_outcome, event_time
            FROM market_resolutions
            WHERE {where}
            ORDER BY condition_id, observed_at ASC
            LIMIT 1 BY condition_id
        )
        ORDER BY event_time DESC
        LIMIT {{limit:UInt32}}
    """
    result = await ch.query(query, parameters=parameters)
    return [(str(r[0]), str(r[1]), r[2]) for r in result.result_rows]


async def run_backtest_smoke(
    ch: AsyncClient,
    *,
    horizon_hours: int = 24,
    resolved_from: datetime | None = None,
    resolved_to: datetime | None = None,
    limit: int = 500,
) -> BacktestReport:
    resolutions = await latest_binary_resolutions(
        ch,
        resolved_from=resolved_from,
        resolved_to=resolved_to,
        limit=limit,
    )
    horizon = timedelta(hours=horizon_hours)
    samples: list[BacktestSample] = []
    skipped = 0

    for condition_id, resolved_outcome, resolved_at in resolutions:
        asked_at = resolved_at - horizon
        detail = await model_for_market(ch, condition_id=condition_id, asked_at=asked_at)
        if detail is None or detail.model_prob is None or detail.mid is None:
            skipped += 1
            continue
        samples.append(
            BacktestSample(
                condition_id=condition_id,
                asked_at=asked_at,
                resolved_at=resolved_at,
                outcome=1 if resolved_outcome == "YES" else 0,
                market_type=detail.market_type,
                model_prob=float(detail.model_prob),
                market_mid=float(detail.mid),
                baseline_source=detail.baseline_source,
            )
        )

    by_type = summarize_samples(samples)
    return BacktestReport(
        horizon_hours=horizon_hours,
        total_resolved_markets=len(resolutions),
        scored_samples=len(samples),
        skipped_samples=skipped,
        by_type=by_type,
    )


def summarize_samples(samples: list[BacktestSample]) -> list[BacktestTypeSummary]:
    grouped: dict[str, list[BacktestSample]] = {}
    for sample in samples:
        grouped.setdefault(sample.market_type, []).append(sample)

    summaries: list[BacktestTypeSummary] = []
    for market_type, group in sorted(grouped.items()):
        baseline_brier = mean(_brier(sample.model_prob, sample.outcome) for sample in group)
        market_mid_brier = mean(_brier(sample.market_mid, sample.outcome) for sample in group)
        skill = None
        if market_mid_brier > 0:
            skill = 1.0 - (baseline_brier / market_mid_brier)
        summaries.append(
            BacktestTypeSummary(
                market_type=market_type,
                samples=len(group),
                baseline_brier=baseline_brier,
                market_mid_brier=market_mid_brier,
                brier_skill_vs_mid=skill,
            )
        )
    return summaries


def _brier(probability: float, outcome: int) -> float:
    p = float(probability)
    y = float(outcome)
    return (p - y) ** 2


def _report_to_json(report: BacktestReport) -> str:
    payload = asdict(report)
    for sample in payload.get("by_type", []):
        for key in ("baseline_brier", "market_mid_brier", "brier_skill_vs_mid"):
            value = sample.get(key)
            if isinstance(value, float):
                sample[key] = round(value, 6)
    return json.dumps(payload, indent=2)


async def _main() -> None:
    ch = await get_async_client()
    try:
        now = datetime.now(tz=UTC)
        report = await run_backtest_smoke(
            ch,
            horizon_hours=24,
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
