from __future__ import annotations

from model import (
    ConformalSample,
    MarketType,
    fit_split_conformal,
    fit_split_conformal_from_folds,
    mondrian_key,
    purged_embargo_splits,
    ttr_bucket,
)


def _sample(
    *,
    market_type: MarketType,
    ttr_s: float | None,
    predicted_prob: float,
    outcome: int,
    asked_at: float,
) -> ConformalSample:
    return ConformalSample(
        market_type=market_type,
        time_to_resolution_s=ttr_s,
        predicted_prob=predicted_prob,
        outcome=outcome,
        asked_at=asked_at,
    )


def test_ttr_bucket_labels() -> None:
    assert ttr_bucket(None) == "unknown"
    assert ttr_bucket(3 * 3600) == "lt_6h"
    assert ttr_bucket(12 * 3600) == "6h_24h"
    assert ttr_bucket(3 * 24 * 3600) == "1d_7d"
    assert ttr_bucket(10 * 24 * 3600) == "gt_7d"


def test_fit_split_conformal_creates_mondrian_cells_and_intervals() -> None:
    registry = fit_split_conformal(
        [
            _sample(
                market_type=MarketType.THRESHOLD,
                ttr_s=2 * 3600,
                predicted_prob=0.20,
                outcome=0,
                asked_at=1,
            ),
            _sample(
                market_type=MarketType.THRESHOLD,
                ttr_s=2 * 3600,
                predicted_prob=0.80,
                outcome=1,
                asked_at=2,
            ),
            _sample(
                market_type=MarketType.THRESHOLD,
                ttr_s=2 * 3600,
                predicted_prob=0.55,
                outcome=1,
                asked_at=3,
            ),
            _sample(
                market_type=MarketType.THRESHOLD,
                ttr_s=10 * 24 * 3600,
                predicted_prob=0.20,
                outcome=1,
                asked_at=4,
            ),
            _sample(
                market_type=MarketType.THRESHOLD,
                ttr_s=10 * 24 * 3600,
                predicted_prob=0.30,
                outcome=1,
                asked_at=5,
            ),
        ],
        coverage=0.8,
    )

    short_key = mondrian_key(MarketType.THRESHOLD, "lt_6h")
    long_key = mondrian_key(MarketType.THRESHOLD, "gt_7d")
    assert short_key in registry.cells
    assert long_key in registry.cells
    assert registry.cells[long_key].quantile > registry.cells[short_key].quantile

    interval = registry.interval(
        predicted_prob=0.60,
        market_type=MarketType.THRESHOLD,
        time_to_resolution_s=2 * 3600,
        uncertainty_multiplier=1.5,
        resolution_risk_multiplier=1.2,
    )

    assert interval is not None
    lo, hi = interval
    assert 0.0 <= lo <= 0.60
    assert 0.60 <= hi <= 1.0


def test_purged_embargo_splits_remove_neighbors() -> None:
    samples = [
        _sample(
            market_type=MarketType.THRESHOLD,
            ttr_s=3600,
            predicted_prob=0.5,
            outcome=idx % 2,
            asked_at=float(idx * 100),
        )
        for idx in range(10)
    ]

    folds = purged_embargo_splits(
        samples,
        n_splits=5,
        purge_window_s=50,
        embargo_s=100,
    )

    assert len(folds) == 5
    first = folds[0]
    assert first.calibration_indices == [0, 1]
    assert 2 not in first.train_indices


def test_fit_split_conformal_from_folds_roundtrips() -> None:
    samples = [
        _sample(
            market_type=MarketType.DISCRETE_EVENT,
            ttr_s=24 * 3600,
            predicted_prob=0.25 + idx * 0.02,
            outcome=1 if idx % 3 == 0 else 0,
            asked_at=float(idx),
        )
        for idx in range(20)
    ]

    registry = fit_split_conformal_from_folds(
        samples,
        coverage=0.8,
        n_splits=4,
        purge_window_s=0,
        embargo_s=0,
    )

    payload = registry.to_json()
    restored = type(registry).from_json(payload)
    interval = restored.interval(
        predicted_prob=0.42,
        market_type=MarketType.DISCRETE_EVENT,
        time_to_resolution_s=24 * 3600,
    )

    assert interval is not None
    assert interval[0] <= 0.42 <= interval[1]
