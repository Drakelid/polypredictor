from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from api import backtest_walk_forward, ensemble_retrain
from model import EnsembleRegistry, EnsembleSample, MarketType


class _Detail:
    def __init__(self, *, ensemble_sample: EnsembleSample | None) -> None:
        self.ensemble_sample = ensemble_sample


def _sample(market_type: MarketType, asked_at: datetime, p_base: float = 0.55) -> EnsembleSample:
    return EnsembleSample(
        market_type=market_type,
        p_base=p_base,
        market_mid=p_base,
        spread=0.02,
        book_imbalance_1pct=0.1,
        book_imbalance_5pct=0.0,
        momentum_1h=0.0,
        momentum_24h=0.0,
        momentum_7d=0.0,
        realized_vol_24h=0.4,
        informed_taker_flow_24h=0.0,
        passive_maker_flow_24h=0.0,
        decayed_directional_flow_24h=0.0,
        smart_money_consensus=0.0,
        holder_concentration=0.1,
        resolution_risk_score=0.0,
        adversarial_flow_score=0.0,
        regime_label="chop",
        asked_at=asked_at.timestamp(),
    )


def _row(condition_id: str, outcome: str, resolved_at: datetime) -> backtest_walk_forward.ResolvedCorpusRow:
    return backtest_walk_forward.ResolvedCorpusRow(
        condition_id=condition_id,
        resolved_outcome=outcome,
        resolved_at=resolved_at,
        resolution_observed_at=resolved_at,
        dispute_status=None,
        volume_usdc=5000.0,
        liquidity_usdc=2000.0,
    )


@pytest.mark.asyncio
async def test_run_retrain_pit_replays_corpus_and_persists_registry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    base = datetime(2026, 4, 10, 12, tzinfo=UTC)
    # Need at least 8 labelled samples per type for fit_per_type_ensembles
    # to fit a model. 5 binary markets x 2 horizons = 10 threshold samples.
    corpus = [
        _row("cond-yes-1", "YES", base),
        _row("cond-yes-2", "YES", base + timedelta(days=1)),
        _row("cond-yes-3", "YES", base + timedelta(days=2)),
        _row("cond-no-1", "NO", base + timedelta(days=3)),
        _row("cond-no-2", "NO", base + timedelta(days=4)),
        _row("cond-invalid", "INVALID", base + timedelta(days=5)),
    ]

    async def _fake_corpus(
        ch: object,
        *,
        resolved_from: datetime | None = None,
        resolved_to: datetime | None = None,
        limit: int = 500,
        low_volume_threshold_usdc: float = 1_000.0,
    ) -> tuple[list[backtest_walk_forward.ResolvedCorpusRow], backtest_walk_forward.CorpusSummary]:
        return corpus, backtest_walk_forward.CorpusSummary(
            total_resolved_markets=6,
            binary_markets=5,
            invalid_markets=1,
            disputed_markets=0,
            low_volume_markets=0,
            missing_snapshot_markets=0,
        )

    async def _fake_model(
        ch: object,
        *,
        condition_id: str,
        asked_at: datetime,
        tuning_profile=None,
    ) -> object | None:
        del tuning_profile
        if condition_id.startswith("cond-yes"):
            return _Detail(ensemble_sample=_sample(MarketType.THRESHOLD, asked_at, 0.7))
        if condition_id.startswith("cond-no"):
            return _Detail(ensemble_sample=_sample(MarketType.THRESHOLD, asked_at, 0.3))
        return None

    monkeypatch.setattr(ensemble_retrain, "load_resolved_market_corpus", _fake_corpus)
    monkeypatch.setattr(ensemble_retrain, "model_for_market", _fake_model)

    output = tmp_path / "registry.json"
    report, _registry = await ensemble_retrain.run_retrain(
        ch=object(),
        horizons_hours=(24, 72),
        resolved_from=base - timedelta(days=30),
        resolved_to=base + timedelta(days=10),
        limit=50,
        output_path=str(output),
    )

    assert report.resolved_corpus_size == 6
    assert report.binary_markets == 5
    # 5 binary markets x 2 horizons = 10 samples; INVALID is skipped.
    assert report.total_samples == 10
    assert report.sample_stats.skipped_invalid_outcome == 1
    assert report.sample_stats.skipped_no_detail == 0
    assert report.sample_stats.by_type == {"threshold": 10}
    assert report.fitted_market_types == ["threshold"]
    assert report.written is True
    assert output.exists()
    parsed = EnsembleRegistry.from_json(output.read_text(encoding="utf-8"))
    assert MarketType.THRESHOLD in parsed.models


@pytest.mark.asyncio
async def test_run_retrain_skips_when_detail_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    base = datetime(2026, 4, 10, 12, tzinfo=UTC)

    async def _fake_corpus(
        ch: object,
        *,
        resolved_from: datetime | None = None,
        resolved_to: datetime | None = None,
        limit: int = 500,
        low_volume_threshold_usdc: float = 1_000.0,
    ) -> tuple[list[backtest_walk_forward.ResolvedCorpusRow], backtest_walk_forward.CorpusSummary]:
        return [_row("cond-yes", "YES", base)], backtest_walk_forward.CorpusSummary(
            total_resolved_markets=1,
            binary_markets=1,
            invalid_markets=0,
            disputed_markets=0,
            low_volume_markets=0,
            missing_snapshot_markets=0,
        )

    async def _fake_model(
        ch: object,
        *,
        condition_id: str,
        asked_at: datetime,
        tuning_profile=None,
    ) -> object | None:
        return None

    monkeypatch.setattr(ensemble_retrain, "load_resolved_market_corpus", _fake_corpus)
    monkeypatch.setattr(ensemble_retrain, "model_for_market", _fake_model)

    report, registry = await ensemble_retrain.run_retrain(
        ch=object(),
        horizons_hours=(24,),
        resolved_from=base - timedelta(days=30),
        resolved_to=base + timedelta(days=10),
        limit=10,
        output_path=None,
        write=False,
    )

    assert report.total_samples == 0
    assert report.sample_stats.skipped_no_detail == 1
    assert report.fitted_market_types == []
    assert registry.models == {}
    assert report.written is False


@pytest.mark.asyncio
async def test_run_retrain_skips_when_detail_lacks_ensemble_sample(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = datetime(2026, 4, 10, 12, tzinfo=UTC)

    async def _fake_corpus(
        ch: object,
        *,
        resolved_from: datetime | None = None,
        resolved_to: datetime | None = None,
        limit: int = 500,
        low_volume_threshold_usdc: float = 1_000.0,
    ) -> tuple[list[backtest_walk_forward.ResolvedCorpusRow], backtest_walk_forward.CorpusSummary]:
        return [_row("cond-yes", "YES", base)], backtest_walk_forward.CorpusSummary(
            total_resolved_markets=1,
            binary_markets=1,
            invalid_markets=0,
            disputed_markets=0,
            low_volume_markets=0,
            missing_snapshot_markets=0,
        )

    async def _fake_model(
        ch: object,
        *,
        condition_id: str,
        asked_at: datetime,
        tuning_profile=None,
    ) -> object | None:
        return _Detail(ensemble_sample=None)

    monkeypatch.setattr(ensemble_retrain, "load_resolved_market_corpus", _fake_corpus)
    monkeypatch.setattr(ensemble_retrain, "model_for_market", _fake_model)

    report, _ = await ensemble_retrain.run_retrain(
        ch=object(),
        horizons_hours=(24,),
        resolved_from=base - timedelta(days=30),
        resolved_to=base + timedelta(days=10),
        limit=10,
        output_path=None,
        write=False,
    )
    assert report.sample_stats.skipped_no_sample == 1
    assert report.total_samples == 0


@pytest.mark.asyncio
async def test_run_retrain_dry_run_does_not_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    base = datetime(2026, 4, 10, 12, tzinfo=UTC)

    async def _fake_corpus(*args: object, **kwargs: object) -> tuple[list[object], object]:
        return [_row("cond-yes", "YES", base)], backtest_walk_forward.CorpusSummary(
            total_resolved_markets=1,
            binary_markets=1,
            invalid_markets=0,
            disputed_markets=0,
            low_volume_markets=0,
            missing_snapshot_markets=0,
        )

    async def _fake_model(
        ch: object,
        *,
        condition_id: str,
        asked_at: datetime,
        tuning_profile=None,
    ) -> object:
        return _Detail(ensemble_sample=_sample(MarketType.THRESHOLD, asked_at))

    monkeypatch.setattr(ensemble_retrain, "load_resolved_market_corpus", _fake_corpus)
    monkeypatch.setattr(ensemble_retrain, "model_for_market", _fake_model)

    output = tmp_path / "registry.json"
    report, _ = await ensemble_retrain.run_retrain(
        ch=object(),
        horizons_hours=(24,),
        resolved_from=base - timedelta(days=30),
        resolved_to=base + timedelta(days=10),
        limit=10,
        output_path=str(output),
        write=False,
    )
    assert report.written is False
    assert not output.exists()


def test_run_retrain_rejects_empty_horizons() -> None:
    import asyncio

    with pytest.raises(ValueError):
        asyncio.run(
            ensemble_retrain.run_retrain(
                ch=object(),
                horizons_hours=(0, -3),
                write=False,
            )
        )
