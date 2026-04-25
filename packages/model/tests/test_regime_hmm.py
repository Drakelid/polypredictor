from __future__ import annotations

from model import RegimeFeatures, RegimeLabel, tag_regime_hmm


def _bull_day() -> RegimeFeatures:
    return RegimeFeatures(
        btc_realized_vol_24h=0.60,
        btc_momentum_7d=0.08,
        stablecoin_supply_delta_7d=0.01,
    )


def _bear_day() -> RegimeFeatures:
    return RegimeFeatures(
        btc_realized_vol_24h=0.60,
        btc_momentum_7d=-0.08,
        stablecoin_supply_delta_7d=-0.01,
    )


def _chop_day() -> RegimeFeatures:
    return RegimeFeatures(
        btc_realized_vol_24h=0.30,
        btc_momentum_7d=0.0,
        stablecoin_supply_delta_7d=0.0,
    )


def _crisis_day() -> RegimeFeatures:
    return RegimeFeatures(
        btc_realized_vol_24h=1.50,
        btc_momentum_7d=-0.10,
        stablecoin_supply_delta_7d=-0.05,
    )


def test_empty_input_returns_empty_list() -> None:
    assert tag_regime_hmm([]) == []


def test_homogeneous_block_decodes_to_its_own_state() -> None:
    # 5-day blocks per regime: long enough that each block's emission
    # likelihood overwhelms the cost of switching state at the boundary.
    block = 5
    sequence = (
        [_bull_day()] * block
        + [_bear_day()] * block
        + [_chop_day()] * block
        + [_crisis_day()] * block
    )
    results = tag_regime_hmm(sequence)
    labels = [result.label for result in results]
    assert labels[2] is RegimeLabel.BULL_TREND
    assert labels[block + 2] is RegimeLabel.BEAR_TREND
    assert labels[2 * block + 2] is RegimeLabel.CHOP
    assert labels[3 * block + 2] is RegimeLabel.LIQUIDITY_CRISIS
    for result in results:
        # softmax-derived confidence is clipped into [0.5, 0.95]
        assert 0.5 <= result.confidence <= 0.95


def test_temporal_smoothing_absorbs_single_day_noise() -> None:
    # Bull regime for two weeks except a single ambiguous day in the middle.
    # The HMM's sticky transition matrix should smooth that day back into
    # BULL_TREND rather than flicker to a different label.
    bull_run = [_bull_day()] * 7
    ambiguous = [
        RegimeFeatures(
            btc_realized_vol_24h=0.45,
            btc_momentum_7d=0.0,
            stablecoin_supply_delta_7d=0.0,
        )
    ]
    sequence = bull_run + ambiguous + bull_run
    results = tag_regime_hmm(sequence)
    assert all(result.label is RegimeLabel.BULL_TREND for result in results)


def test_persistent_regime_change_is_followed() -> None:
    # Five bull days, then five crisis days. The HMM should switch — the
    # stickiness penalty is < 5 days of strong crisis emissions.
    sequence = [_bull_day()] * 5 + [_crisis_day()] * 5
    results = tag_regime_hmm(sequence)
    assert all(r.label is RegimeLabel.BULL_TREND for r in results[:3])
    assert results[-1].label is RegimeLabel.LIQUIDITY_CRISIS


def test_partial_features_still_emit_a_label() -> None:
    # Only realized vol observed — emission likelihood degrades to a
    # vol-only Gaussian but Viterbi still produces a valid sequence.
    sequence = [
        RegimeFeatures(btc_realized_vol_24h=0.30, btc_momentum_7d=None),
        RegimeFeatures(btc_realized_vol_24h=0.32, btc_momentum_7d=None),
    ]
    results = tag_regime_hmm(sequence)
    assert len(results) == 2
    assert all(result.label in set(RegimeLabel) for result in results)


def test_all_missing_features_produces_uniform_emission() -> None:
    # When every feature is None the emission is flat across states; the HMM
    # should still emit *something*, with a confidence that reflects the
    # uncertainty (close to 0.5 — the floor).
    sequence = [RegimeFeatures()] * 3
    results = tag_regime_hmm(sequence)
    assert len(results) == 3
    for result in results:
        assert 0.5 <= result.confidence <= 0.95


def test_thresholds_argument_is_documented_in_reasons() -> None:
    # Backwards-compat with the rule classifier signature: thresholds is
    # accepted but doesn't affect decoding. We surface that in `reasons` so
    # callers don't silently expect threshold overrides.
    from model import RegimeThresholds

    result = tag_regime_hmm([_bull_day()], thresholds=RegimeThresholds())
    assert any("RegimeThresholds" in reason for reason in result[0].reasons)


def test_reasons_include_observed_features() -> None:
    result = tag_regime_hmm([_crisis_day()])[0]
    assert any("realized vol" in reason for reason in result.reasons)
    assert any("momentum" in reason for reason in result.reasons)
    assert any("stablecoin" in reason for reason in result.reasons)
