from __future__ import annotations

from model import (
    ConformalSample,
    MarketType,
    fit_split_conformal,
    mondrian_key,
    mondrian_key_with_regime,
)


def _sample(
    *,
    p: float,
    y: int,
    ttr_s: float = 3 * 86_400.0,
    type_: MarketType = MarketType.THRESHOLD,
    regime: str | None = None,
    asked_at: float = 0.0,
) -> ConformalSample:
    return ConformalSample(
        market_type=type_,
        time_to_resolution_s=ttr_s,
        predicted_prob=p,
        outcome=y,
        asked_at=asked_at,
        regime=regime,
    )


def test_fit_emits_regime_aware_cells_when_samples_carry_regime() -> None:
    # Crisis residuals are LARGER than chop residuals; the regime axis lets
    # the registry emit a wider band when serving in crisis.
    samples = (
        [_sample(p=0.3, y=1, regime="liquidity_crisis") for _ in range(20)]  # |0.3-1|=0.7
        + [_sample(p=0.55, y=1, regime="chop") for _ in range(20)]            # |0.55-1|=0.45
    )
    registry = fit_split_conformal(samples, coverage=0.8)

    crisis_key = mondrian_key_with_regime(
        MarketType.THRESHOLD, "1d_7d", "liquidity_crisis"
    )
    chop_key = mondrian_key_with_regime(MarketType.THRESHOLD, "1d_7d", "chop")
    assert crisis_key in registry.cells
    assert chop_key in registry.cells
    assert registry.cells[crisis_key].quantile > registry.cells[chop_key].quantile


def test_cell_for_uses_regime_specific_when_present() -> None:
    samples = (
        [_sample(p=0.25, y=1, regime="bull_trend") for _ in range(20)]   # residual 0.75
        + [_sample(p=0.45, y=1, regime="chop") for _ in range(20)]       # residual 0.55
    )
    registry = fit_split_conformal(samples, coverage=0.8)

    bull = registry.cell_for(
        market_type=MarketType.THRESHOLD,
        time_to_resolution_s=3 * 86_400.0,
        regime="bull_trend",
    )
    chop = registry.cell_for(
        market_type=MarketType.THRESHOLD,
        time_to_resolution_s=3 * 86_400.0,
        regime="chop",
    )
    none = registry.cell_for(
        market_type=MarketType.THRESHOLD,
        time_to_resolution_s=3 * 86_400.0,
    )
    assert bull is not None and chop is not None and none is not None
    # Different regimes produce different quantiles; the no-regime fallback
    # uses the type/ttr cell that pools both regimes.
    assert bull.quantile != chop.quantile
    # The pooled no-regime cell should land between the two regime extremes.
    assert min(bull.quantile, chop.quantile) <= none.quantile <= max(bull.quantile, chop.quantile)


def test_cell_for_unknown_regime_falls_back_to_non_regime_cell() -> None:
    samples = [_sample(p=0.5, y=1, regime="chop") for _ in range(15)]
    registry = fit_split_conformal(samples, coverage=0.8)
    # Asking for a regime that wasn't in the calibration set should NOT
    # return None — it should degrade through the non-regime fallback chain.
    fallback = registry.cell_for(
        market_type=MarketType.THRESHOLD,
        time_to_resolution_s=3 * 86_400.0,
        regime="some_regime_not_in_cal_set",
    )
    no_regime = registry.cell_for(
        market_type=MarketType.THRESHOLD,
        time_to_resolution_s=3 * 86_400.0,
    )
    assert fallback is not None
    assert no_regime is not None
    assert fallback.key == no_regime.key  # fell through to the same cell


def test_interval_passes_regime_through_to_cell_for() -> None:
    # Residuals deliberately small enough that the [0, 1] clipping does not
    # saturate either band — otherwise both widths land at 1.0 and the test
    # cannot distinguish them.
    crisis_calls = [_sample(p=0.6, y=1, regime="liquidity_crisis") for _ in range(20)]  # 0.4
    chop_calls = [_sample(p=0.7, y=1, regime="chop") for _ in range(20)]                # 0.3
    registry = fit_split_conformal(crisis_calls + chop_calls, coverage=0.8)

    crisis_band = registry.interval(
        predicted_prob=0.5,
        market_type=MarketType.THRESHOLD,
        time_to_resolution_s=3 * 86_400.0,
        regime="liquidity_crisis",
    )
    chop_band = registry.interval(
        predicted_prob=0.5,
        market_type=MarketType.THRESHOLD,
        time_to_resolution_s=3 * 86_400.0,
        regime="chop",
    )
    assert crisis_band is not None and chop_band is not None
    crisis_width = crisis_band[1] - crisis_band[0]
    chop_width = chop_band[1] - chop_band[0]
    # Crisis residuals are larger by construction → wider band.
    assert crisis_width > chop_width


def test_legacy_two_segment_keys_unchanged_for_backward_compat() -> None:
    samples = [_sample(p=0.5, y=1) for _ in range(15)]
    registry = fit_split_conformal(samples, coverage=0.8)
    assert mondrian_key(MarketType.THRESHOLD, "1d_7d") in registry.cells
    # No regime samples → no regime-keyed cells.
    assert not any(
        key.count(":") >= 2 for key in registry.cells if key.endswith(":chop")
    )
