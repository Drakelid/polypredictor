from __future__ import annotations

import pytest
from model import (
    RegimeFeatures,
    RegimeLabel,
    RegimeThresholds,
    regime_features_from_btc_closes,
    tag_regime,
)


def test_stablecoin_contraction_overrides_to_liquidity_crisis() -> None:
    # Even a benign vol + bullish momentum reading is overridden by a
    # 5% weekly stablecoin supply contraction.
    result = tag_regime(
        RegimeFeatures(
            btc_realized_vol_24h=0.5,
            btc_momentum_7d=0.10,
            stablecoin_supply_delta_7d=-0.05,
        )
    )
    assert result.label is RegimeLabel.LIQUIDITY_CRISIS
    assert result.confidence == pytest.approx(0.9)
    assert any("stablecoin" in r for r in result.reasons)


def test_extreme_realized_vol_triggers_crisis_when_supply_unknown() -> None:
    result = tag_regime(
        RegimeFeatures(btc_realized_vol_24h=1.5, btc_momentum_7d=0.02)
    )
    assert result.label is RegimeLabel.LIQUIDITY_CRISIS
    assert result.confidence == pytest.approx(0.75)


def test_low_realized_vol_classifies_as_chop() -> None:
    result = tag_regime(
        RegimeFeatures(btc_realized_vol_24h=0.20, btc_momentum_7d=0.10)
    )
    assert result.label is RegimeLabel.CHOP
    assert any("chop" in r for r in result.reasons)


def test_positive_momentum_with_moderate_vol_is_bull_trend() -> None:
    result = tag_regime(
        RegimeFeatures(btc_realized_vol_24h=0.55, btc_momentum_7d=0.06)
    )
    assert result.label is RegimeLabel.BULL_TREND
    assert result.confidence > 0.6


def test_negative_momentum_with_moderate_vol_is_bear_trend() -> None:
    result = tag_regime(
        RegimeFeatures(btc_realized_vol_24h=0.55, btc_momentum_7d=-0.07)
    )
    assert result.label is RegimeLabel.BEAR_TREND


def test_momentum_inside_band_is_chop_with_low_confidence() -> None:
    result = tag_regime(
        RegimeFeatures(btc_realized_vol_24h=0.55, btc_momentum_7d=0.01)
    )
    assert result.label is RegimeLabel.CHOP
    assert result.confidence < 0.7


def test_missing_momentum_falls_back_to_chop() -> None:
    result = tag_regime(
        RegimeFeatures(btc_realized_vol_24h=0.55, btc_momentum_7d=None)
    )
    assert result.label is RegimeLabel.CHOP
    assert any("no 7d momentum" in r for r in result.reasons)


def test_ndx_correlation_appears_in_reasons_when_provided() -> None:
    result = tag_regime(
        RegimeFeatures(
            btc_realized_vol_24h=0.55,
            btc_momentum_7d=0.01,
            btc_ndx_correlation_30d=0.62,
        )
    )
    assert any("BTC↔NDX" in r for r in result.reasons)


def test_thresholds_can_be_overridden() -> None:
    # A 2% weekly contraction would be ignored by default but trips a
    # tighter threshold.
    strict = RegimeThresholds(stablecoin_contraction_fraction=-0.01)
    result = tag_regime(
        RegimeFeatures(
            btc_realized_vol_24h=0.5,
            btc_momentum_7d=0.05,
            stablecoin_supply_delta_7d=-0.02,
        ),
        thresholds=strict,
    )
    assert result.label is RegimeLabel.LIQUIDITY_CRISIS


def test_regime_features_from_btc_closes_steady_uptrend_signals_bull_momentum() -> None:
    # 0.4% daily growth for 14 days → ~5.6% over 7d, vol low.
    closes = [100.0 * (1.004 ** i) for i in range(14)]
    features = regime_features_from_btc_closes(closes)
    assert features.btc_momentum_7d is not None
    assert features.btc_momentum_7d > 0.025
    assert features.btc_realized_vol_7d is not None
    assert features.btc_realized_vol_7d < 0.30


def test_regime_features_from_btc_closes_volatility_proportional_to_jumps() -> None:
    quiet = [100.0, 100.1, 100.0, 100.05, 100.02, 100.08, 100.04, 100.12]
    wild = [100.0, 105.0, 95.0, 108.0, 92.0, 110.0, 88.0, 112.0]
    quiet_vol = regime_features_from_btc_closes(quiet).btc_realized_vol_7d
    wild_vol = regime_features_from_btc_closes(wild).btc_realized_vol_7d
    assert quiet_vol is not None and wild_vol is not None
    assert wild_vol > quiet_vol * 10


def test_regime_features_from_btc_closes_too_short_returns_only_passthroughs() -> None:
    features = regime_features_from_btc_closes(
        [100.0],
        stablecoin_supply_delta_7d=-0.04,
    )
    assert features.btc_realized_vol_24h is None
    assert features.btc_momentum_7d is None
    assert features.stablecoin_supply_delta_7d == pytest.approx(-0.04)


def test_regime_features_from_btc_closes_skips_non_positive_values() -> None:
    # Garbage in (0 / negative) should be filtered before log returns —
    # otherwise the log blows up.
    closes = [100.0, 0.0, 101.0, -5.0, 102.0, 103.0, 104.0, 105.0, 106.0]
    features = regime_features_from_btc_closes(closes)
    assert features.btc_realized_vol_7d is not None
    assert features.btc_momentum_7d is not None


def test_bull_confidence_saturates_for_strong_momentum() -> None:
    weak = tag_regime(
        RegimeFeatures(btc_realized_vol_24h=0.55, btc_momentum_7d=0.04)
    )
    strong = tag_regime(
        RegimeFeatures(btc_realized_vol_24h=0.55, btc_momentum_7d=0.20)
    )
    assert weak.label is RegimeLabel.BULL_TREND
    assert strong.label is RegimeLabel.BULL_TREND
    assert strong.confidence > weak.confidence
    assert strong.confidence <= 0.95
