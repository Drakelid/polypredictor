"""Per-type ensemble retraining pipeline.

PIT-replays the resolved-market corpus through ``model_for_market`` to
collect outcome-labelled :class:`EnsembleSample` rows, fits per-type
models via :func:`fit_per_type_ensembles`, and persists the resulting
:class:`EnsembleRegistry` to ``settings.ensemble_registry_file``.

This is the missing trigger flagged in ``project_status`` so the new
onchain + macro booster features actually get stumps fit instead of
sitting dormant in legacy registries.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from clickhouse_connect.driver.asyncclient import AsyncClient
from model import EnsembleRegistry, EnsembleSample, fit_per_type_ensembles

from .backtest_walk_forward import load_resolved_market_corpus
from .clickhouse import get_async_client
from .markets import model_for_market
from .settings import get_settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetrainSampleStats:
    by_type: dict[str, int]
    skipped_no_detail: int
    skipped_no_sample: int
    skipped_invalid_outcome: int


@dataclass(frozen=True)
class RetrainReport:
    horizons_hours: list[int]
    resolved_corpus_size: int
    binary_markets: int
    total_samples: int
    sample_stats: RetrainSampleStats
    fitted_market_types: list[str]
    output_path: str | None
    written: bool


async def _samples_for_corpus(
    ch: AsyncClient,
    *,
    resolved_from: datetime,
    resolved_to: datetime,
    horizons_hours: list[int],
    limit: int,
) -> tuple[list[EnsembleSample], RetrainSampleStats, int, int]:
    corpus_rows, summary = await load_resolved_market_corpus(
        ch,
        resolved_from=resolved_from,
        resolved_to=resolved_to,
        limit=limit,
    )
    samples: list[EnsembleSample] = []
    by_type: Counter[str] = Counter()
    skipped_no_detail = 0
    skipped_no_sample = 0
    skipped_invalid_outcome = 0

    for row in corpus_rows:
        if row.resolved_outcome not in {"YES", "NO"}:
            skipped_invalid_outcome += 1
            continue
        outcome = 1 if row.resolved_outcome == "YES" else 0
        for horizon in horizons_hours:
            asked_at = row.resolved_at - timedelta(hours=horizon)
            detail = await model_for_market(
                ch,
                condition_id=row.condition_id,
                asked_at=asked_at,
            )
            if detail is None:
                skipped_no_detail += 1
                continue
            sample = detail.ensemble_sample
            if sample is None:
                skipped_no_sample += 1
                continue
            labelled = dataclasses.replace(sample, outcome=outcome)
            samples.append(labelled)
            by_type[labelled.market_type.value] += 1

    stats = RetrainSampleStats(
        by_type=dict(by_type),
        skipped_no_detail=skipped_no_detail,
        skipped_no_sample=skipped_no_sample,
        skipped_invalid_outcome=skipped_invalid_outcome,
    )
    return samples, stats, summary.total_resolved_markets, summary.binary_markets


def _write_registry(path: Path, registry: EnsembleRegistry) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(registry.to_json(), encoding="utf-8")


async def run_retrain(
    ch: AsyncClient,
    *,
    horizons_hours: list[int] | tuple[int, ...] = (24, 72, 168),
    resolved_from: datetime | None = None,
    resolved_to: datetime | None = None,
    limit: int = 500,
    output_path: str | None = None,
    write: bool = True,
) -> tuple[RetrainReport, EnsembleRegistry]:
    """Replay the resolved corpus and refit per-type ensembles.

    Parameters
    ----------
    horizons_hours:
        Pre-resolution horizons to sample at. Each (market, horizon) pair
        produces one ``EnsembleSample`` when a PIT detail is available.
    resolved_from / resolved_to:
        Resolution-window bounds. Default is trailing 90 days.
    limit:
        Maximum number of resolved markets to load.
    output_path:
        Where to persist the registry. Defaults to
        ``settings.ensemble_registry_file``.
    write:
        When false, the registry is fitted but not persisted (dry run).
    """
    settings = get_settings()
    horizons = [int(h) for h in horizons_hours if int(h) > 0]
    if not horizons:
        raise ValueError("at least one positive horizon is required")

    now = datetime.now(tz=UTC)
    if resolved_to is None:
        resolved_to = now
    if resolved_from is None:
        resolved_from = resolved_to - timedelta(days=90)

    target_path = output_path or settings.ensemble_registry_file
    samples, sample_stats, corpus_size, binary_markets = await _samples_for_corpus(
        ch,
        resolved_from=resolved_from,
        resolved_to=resolved_to,
        horizons_hours=horizons,
        limit=limit,
    )

    registry = fit_per_type_ensembles(samples)

    written = False
    if write and target_path:
        _write_registry(Path(target_path), registry)
        written = True

    fitted_types = sorted(market_type.value for market_type in registry.models)
    report = RetrainReport(
        horizons_hours=horizons,
        resolved_corpus_size=corpus_size,
        binary_markets=binary_markets,
        total_samples=len(samples),
        sample_stats=sample_stats,
        fitted_market_types=fitted_types,
        output_path=target_path,
        written=written,
    )
    return report, registry


async def _main() -> None:
    logging.basicConfig(level=logging.INFO)
    ch = await get_async_client()
    try:
        report, _ = await run_retrain(ch)
        log.info(
            "ensemble_retrain.done",
            extra={
                "samples": report.total_samples,
                "fitted_types": report.fitted_market_types,
                "output_path": report.output_path,
                "written": report.written,
                "by_type": report.sample_stats.by_type,
            },
        )
        print(json.dumps(dataclasses.asdict(report), indent=2, default=str))
    finally:
        await ch.close()


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()


__all__ = [
    "RetrainReport",
    "RetrainSampleStats",
    "run_retrain",
]
