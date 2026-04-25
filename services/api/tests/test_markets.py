from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from api.adversarial_flow import AdversarialFlowContext
from api.asof import MarketSnapshotRow, QuoteRow
from api.discrete_inputs import DiscreteBaselineRecord
from api.features import FeatureSnapshotRow
from api.long_tail_priors import LongTailPriorContext
from api.markets import (
    BaselineContext,
    DiscreteBaselineConfig,
    MultiOutcomeContext,
    _apply_conformal_interval,
    _apply_ensemble_refinement,
    _baseline_inputs_for,
    _build_multi_outcome_contexts,
    _edge_bps_with_resolution_risk,
    _feature_attributions_for_sample,
    _kelly_recommendation,
    _sample_history_rows,
    history_for_market,
)
from api.resolution_risk import ResolutionRiskRow
from model import (
    BOOSTER_FEATURE_NAMES,
    BaselineOutput,
    BaselineSource,
    ClassificationResult,
    ConformalCell,
    DeribitIV,
    EnsembleRegistry,
    EnsembleSample,
    MarketFeatures,
    MarketType,
    PipelineResult,
    SplitConformalRegistry,
)


class _FakeDeribit:
    def __init__(self, iv: DeribitIV | None) -> None:
        self.iv = iv
        self.calls: list[tuple[str, float, str, float]] = []

    async def fetch_iv(
        self,
        *,
        currency: str,
        strike: float,
        direction: str,
        horizon_days: float,
    ) -> DeribitIV | None:
        self.calls.append((currency, strike, direction, horizon_days))
        return self.iv


class _FakeFedWatch:
    def __init__(self, record: DiscreteBaselineRecord | None) -> None:
        self.record = record
        self.calls: list[tuple[str, str | None]] = []

    async def implied_record_for_market(
        self,
        *,
        question: str,
        slug: str | None = None,
        now: datetime | None = None,
    ) -> DiscreteBaselineRecord | None:
        self.calls.append((question, slug))
        return self.record


@pytest.mark.asyncio
async def test_threshold_market_uses_live_deribit_inputs() -> None:
    classification = ClassificationResult(
        market_type=MarketType.THRESHOLD,
        confidence=0.9,
        features=MarketFeatures(
            asset="BTC",
            strike=150_000,
            direction="above",
            resolution_date=datetime(2026, 7, 22, tzinfo=UTC),
        ),
        reasons=["threshold"],
    )
    deribit = _FakeDeribit(
        DeribitIV(
            index_price=100_000,
            expiry_days=90,
            realized_vol=0.42,
            atm_iv=0.55,
            strike_iv=0.68,
            atm_instrument="BTC-31JUL26-100000-C",
            strike_instrument="BTC-31JUL26-150000-C",
        )
    )

    inputs = await _baseline_inputs_for(
        classification=classification,
        snap={"condition_id": "cond-1", "slug": "btc-150k", "question": "Will BTC reach $150k?"},
        market_mid=0.42,
        asked_at=datetime(2026, 4, 22, tzinfo=UTC),
        deribit=deribit,
        fedwatch=None,
        discrete=DiscreteBaselineConfig(),
    )

    assert inputs.spot == pytest.approx(100_000)
    assert inputs.realized_vol == pytest.approx(0.42)
    assert inputs.implied_vol_atm == pytest.approx(0.55)
    assert inputs.implied_vol_strike == pytest.approx(0.68)
    assert inputs.market_mid == pytest.approx(0.42)
    assert len(deribit.calls) == 1
    currency, strike, direction, horizon_days = deribit.calls[0]
    assert currency == "BTC"
    assert strike == pytest.approx(150_000.0)
    assert direction == "above"
    assert horizon_days == pytest.approx(91.0, abs=1.0)


@pytest.mark.asyncio
async def test_discrete_event_uses_configured_consensus_placeholder() -> None:
    classification = ClassificationResult(
        market_type=MarketType.DISCRETE_EVENT,
        confidence=0.85,
        features=MarketFeatures(
            event_family="cpi",
            strike=2.8,
            direction="below",
        ),
        reasons=["event_family=cpi"],
    )

    inputs = await _baseline_inputs_for(
        classification=classification,
        snap={"condition_id": "cond-cpi", "slug": "cpi-june", "question": "Will CPI print below 2.8% in June?"},
        market_mid=0.25,
        asked_at=datetime(2026, 4, 22, tzinfo=UTC),
        deribit=None,
        fedwatch=None,
        discrete=DiscreteBaselineConfig(
            cpi_consensus_point=3.0,
            cpi_consensus_stddev=0.2,
        ),
    )

    assert inputs.consensus_point == pytest.approx(3.0)
    assert inputs.consensus_stddev == pytest.approx(0.2)
    assert inputs.market_mid == pytest.approx(0.25)


@pytest.mark.asyncio
async def test_discrete_event_prefers_file_backed_market_override(tmp_path) -> None:
    path = tmp_path / "discrete.json"
    path.write_text(
        json.dumps(
            {
                "families": {"fomc": {"fedwatch_prob": 0.72}},
                "markets": [
                    {
                        "slug": "fed-cuts-june-2026",
                        "event_family": "fomc",
                        "fedwatch_prob": 0.68,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    classification = ClassificationResult(
        market_type=MarketType.DISCRETE_EVENT,
        confidence=0.85,
        features=MarketFeatures(event_family="fomc"),
        reasons=["event_family=fomc"],
    )

    inputs = await _baseline_inputs_for(
        classification=classification,
        snap={
            "condition_id": "cond-fed",
            "slug": "fed-cuts-june-2026",
            "question": "Will the Fed cut in June 2026?",
        },
        market_mid=0.3,
        asked_at=datetime(2026, 4, 22, tzinfo=UTC),
        deribit=None,
        fedwatch=None,
        discrete=DiscreteBaselineConfig(
            baselines_file=str(path),
            fomc_fedwatch_prob=0.55,
        ),
    )

    assert inputs.fedwatch_prob == pytest.approx(0.68)


@pytest.mark.asyncio
async def test_discrete_event_uses_live_fedwatch_when_no_file_or_env_value() -> None:
    classification = ClassificationResult(
        market_type=MarketType.DISCRETE_EVENT,
        confidence=0.85,
        features=MarketFeatures(event_family="fomc"),
        reasons=["event_family=fomc"],
    )
    fedwatch = _FakeFedWatch(
        DiscreteBaselineRecord(
            fedwatch_prob=0.61,
            source="cme_fedwatch:cut",
            as_of="2026-04-22",
        )
    )

    inputs = await _baseline_inputs_for(
        classification=classification,
        snap={
            "condition_id": "cond-fed",
            "slug": "fed-cuts-june-2026",
            "question": "Will the Fed cut in June 2026?",
        },
        market_mid=0.3,
        asked_at=datetime(2026, 4, 22, tzinfo=UTC),
        deribit=None,
        fedwatch=fedwatch,
        discrete=DiscreteBaselineConfig(),
    )

    assert inputs.fedwatch_prob == pytest.approx(0.61)
    assert fedwatch.calls == [("Will the Fed cut in June 2026?", "fed-cuts-june-2026")]


def test_build_multi_outcome_contexts_uses_sorted_siblings_and_yes_mids() -> None:
    contexts = _build_multi_outcome_contexts(
        {
            "evt-1": [
                {"condition_id": "cond-b", "token_ids": ["tok-b"]},
                {"condition_id": "cond-a", "token_ids": ["tok-a"]},
                {"condition_id": "cond-c", "token_ids": ["tok-c"]},
            ]
        },
        {"tok-a": 0.52, "tok-b": 0.33, "tok-c": 0.15},
    )

    assert contexts["cond-a"] == MultiOutcomeContext(raw=[0.52, 0.33, 0.15], self_index=0)
    assert contexts["cond-b"] == MultiOutcomeContext(raw=[0.52, 0.33, 0.15], self_index=1)
    assert contexts["cond-c"] == MultiOutcomeContext(raw=[0.52, 0.33, 0.15], self_index=2)


@pytest.mark.asyncio
async def test_multi_outcome_market_uses_sibling_context() -> None:
    classification = ClassificationResult(
        market_type=MarketType.MULTI_OUTCOME,
        confidence=0.9,
        features=MarketFeatures(is_multi_outcome_sibling=True),
        reasons=["multi_outcome"],
    )

    inputs = await _baseline_inputs_for(
        classification=classification,
        snap={
            "condition_id": "cond-b",
            "slug": "winner",
            "question": "Who wins?",
        },
        market_mid=0.33,
        asked_at=datetime(2026, 4, 22, tzinfo=UTC),
        deribit=None,
        fedwatch=None,
        discrete=DiscreteBaselineConfig(),
        context=BaselineContext(
            multi_outcome=MultiOutcomeContext(raw=[0.52, 0.33, 0.15], self_index=1)
        ),
    )

    assert inputs.market_mid == pytest.approx(0.33)
    assert inputs.multi_outcome_raw == [0.52, 0.33, 0.15]
    assert inputs.multi_outcome_self_index == 1


@pytest.mark.asyncio
async def test_long_tail_market_uses_analog_and_base_rate_priors() -> None:
    classification = ClassificationResult(
        market_type=MarketType.LONG_TAIL_BINARY,
        confidence=0.6,
        features=MarketFeatures(),
        reasons=["long-tail binary phrasing"],
    )

    inputs = await _baseline_inputs_for(
        classification=classification,
        snap={
            "condition_id": "cond-long",
            "slug": "coinbase-hacked-2026",
            "question": "Will Coinbase be hacked in 2026?",
        },
        market_mid=0.08,
        asked_at=datetime(2026, 4, 22, tzinfo=UTC),
        deribit=None,
        fedwatch=None,
        discrete=DiscreteBaselineConfig(),
        context=BaselineContext(
            long_tail=LongTailPriorContext(
                embedding_prior=0.14,
                historical_base_rate=0.06,
            )
        ),
    )

    assert inputs.embedding_prior == pytest.approx(0.14)
    assert inputs.historical_base_rate == pytest.approx(0.06)
    assert inputs.market_mid == pytest.approx(0.08)


def _ensemble_registry_payload() -> EnsembleRegistry:
    booster_means = {name: 0.0 for name in BOOSTER_FEATURE_NAMES}
    booster_scales = {name: 1.0 for name in BOOSTER_FEATURE_NAMES}
    return EnsembleRegistry.from_dict(
        {
            "models": {
                "threshold": {
                    "market_type": "threshold",
                    "linear_means": {"p_base_logit": 0.0, "market_mid_logit": 0.0},
                    "linear_scales": {"p_base_logit": 1.0, "market_mid_logit": 1.0},
                    "booster_means": booster_means,
                    "booster_scales": booster_scales,
                    "linear_intercept": 0.1,
                    "linear_weights": {"p_base_logit": 0.7, "market_mid_logit": 0.0},
                    "stumps": [],
                    "calibrator": {"upper_bounds": [0.5, 1.0], "values": [0.35, 0.78]},
                    "market_mid_weight_cap": 0.35,
                }
            }
        }
    )


def test_apply_ensemble_refinement_uses_registry_and_feature_snapshot() -> None:
    classification = type("ClsRow", (), {"market_type": MarketType.THRESHOLD})()
    pipeline_result = PipelineResult(
        classification=ClassificationResult(
            market_type=MarketType.THRESHOLD,
            confidence=0.9,
            features=MarketFeatures(asset="BTC", strike=150_000, direction="above"),
            reasons=["threshold"],
        ),
        baseline=BaselineOutput(
            probability=0.42,
            source=BaselineSource.BS_ONE_TOUCH,
            reasons=["threshold baseline"],
        ),
        displayed_probability=0.42,
        displayed_source=BaselineSource.BS_ONE_TOUCH,
        _mid=0.39,
    )
    feature_snapshot = FeatureSnapshotRow(
        condition_id="cond-1",
        token_id="tok-yes",
        mid=0.39,
        spread=0.03,
        book_imbalance_1pct=0.2,
        book_imbalance_5pct=0.15,
        momentum_1h=0.02,
        momentum_24h=0.04,
        momentum_7d=0.06,
        realized_vol_24h=0.45,
        informed_taker_flow_24h=0.12,
        passive_maker_flow_24h=-0.03,
        decayed_directional_flow_24h=0.08,
        event_time=datetime(2026, 4, 22, tzinfo=UTC),
        observed_at=datetime(2026, 4, 22, 12, tzinfo=UTC),
    )

    model_prob, model_source, refinement_source, model_reasons = _apply_ensemble_refinement(
        registry=_ensemble_registry_payload(),
        classification=classification,
        pipeline_result=pipeline_result,
        market_mid=0.39,
        sibling_prior=None,
        feature_snapshot=feature_snapshot,
        smart_money=None,
        concentration=None,
        adversarial_flow=None,
        concentration_threshold=0.6,
        adversarial_flow_blend_floor=0.25,
        asked_at=datetime(2026, 4, 22, 12, tzinfo=UTC),
    )

    assert model_source == "ensemble"
    assert refinement_source == "per_type_ensemble_v1"
    assert model_prob == pytest.approx(0.35)
    assert model_reasons[0] == "Per-type ensemble refinement over bs_one_touch"


def test_apply_ensemble_refinement_uses_regime_feature_when_available() -> None:
    classification = type("ClsRow", (), {"market_type": MarketType.THRESHOLD})()
    pipeline_result = PipelineResult(
        classification=ClassificationResult(
            market_type=MarketType.THRESHOLD,
            confidence=0.9,
            features=MarketFeatures(asset="BTC", strike=150_000, direction="above"),
            reasons=["threshold"],
        ),
        baseline=BaselineOutput(
            probability=0.50,
            source=BaselineSource.BS_ONE_TOUCH,
            reasons=["threshold baseline"],
        ),
        displayed_probability=0.50,
        displayed_source=BaselineSource.BS_ONE_TOUCH,
        _mid=0.50,
    )
    feature_snapshot = FeatureSnapshotRow(
        condition_id="cond-1",
        token_id="tok-yes",
        mid=0.50,
        spread=0.02,
        book_imbalance_1pct=0.0,
        book_imbalance_5pct=0.0,
        momentum_1h=0.0,
        momentum_24h=0.0,
        momentum_7d=0.0,
        realized_vol_24h=0.45,
        informed_taker_flow_24h=0.0,
        passive_maker_flow_24h=0.0,
        decayed_directional_flow_24h=0.0,
        event_time=datetime(2026, 4, 22, tzinfo=UTC),
        observed_at=datetime(2026, 4, 22, 12, tzinfo=UTC),
    )
    booster_means = {name: 0.0 for name in BOOSTER_FEATURE_NAMES}
    booster_scales = {name: 1.0 for name in BOOSTER_FEATURE_NAMES}
    registry = EnsembleRegistry.from_dict(
        {
            "models": {
                "threshold": {
                    "market_type": "threshold",
                    "linear_means": {"p_base_logit": 0.0, "market_mid_logit": 0.0},
                    "linear_scales": {"p_base_logit": 1.0, "market_mid_logit": 1.0},
                    "booster_means": booster_means,
                    "booster_scales": booster_scales,
                    "linear_intercept": 0.0,
                    "linear_weights": {"p_base_logit": 0.0, "market_mid_logit": 0.0},
                    "stumps": [
                        {
                            "feature_name": "regime_bull_trend",
                            "threshold": 0.5,
                            "left_value": -1.0,
                            "right_value": 1.0,
                        }
                    ],
                    "calibrator": {
                        "upper_bounds": [0.5, 1.0],
                        "values": [0.25, 0.75],
                    },
                    "market_mid_weight_cap": 0.35,
                }
            }
        }
    )

    bull_prob, bull_source, bull_refinement, _ = _apply_ensemble_refinement(
        registry=registry,
        classification=classification,
        pipeline_result=pipeline_result,
        market_mid=0.50,
        sibling_prior=None,
        feature_snapshot=feature_snapshot,
        smart_money=None,
        concentration=None,
        adversarial_flow=None,
        regime_label="bull_trend",
        concentration_threshold=0.6,
        adversarial_flow_blend_floor=0.25,
        asked_at=datetime(2026, 4, 22, 12, tzinfo=UTC),
    )
    bear_prob, _, _, _ = _apply_ensemble_refinement(
        registry=registry,
        classification=classification,
        pipeline_result=pipeline_result,
        market_mid=0.50,
        sibling_prior=None,
        feature_snapshot=feature_snapshot,
        smart_money=None,
        concentration=None,
        adversarial_flow=None,
        regime_label="bear_trend",
        concentration_threshold=0.6,
        adversarial_flow_blend_floor=0.25,
        asked_at=datetime(2026, 4, 22, 12, tzinfo=UTC),
    )

    assert bull_source == "ensemble"
    assert bull_refinement == "per_type_ensemble_v1"
    assert bull_prob is not None and bear_prob is not None
    assert bull_prob > bear_prob


def test_apply_ensemble_refinement_falls_back_without_feature_snapshot() -> None:
    classification = type("ClsRow", (), {"market_type": MarketType.THRESHOLD})()
    pipeline_result = PipelineResult(
        classification=ClassificationResult(
            market_type=MarketType.THRESHOLD,
            confidence=0.9,
            features=MarketFeatures(asset="BTC", strike=150_000, direction="above"),
            reasons=["threshold"],
        ),
        baseline=BaselineOutput(
            probability=0.42,
            source=BaselineSource.BS_ONE_TOUCH,
            reasons=["threshold baseline"],
        ),
        displayed_probability=0.42,
        displayed_source=BaselineSource.BS_ONE_TOUCH,
        _mid=0.39,
    )

    model_prob, model_source, refinement_source, model_reasons = _apply_ensemble_refinement(
        registry=_ensemble_registry_payload(),
        classification=classification,
        pipeline_result=pipeline_result,
        market_mid=0.39,
        sibling_prior=None,
        feature_snapshot=None,
        smart_money=None,
        concentration=None,
        adversarial_flow=None,
        concentration_threshold=0.6,
        adversarial_flow_blend_floor=0.25,
        asked_at=datetime(2026, 4, 22, 12, tzinfo=UTC),
    )

    assert model_prob == pytest.approx(0.42)
    assert model_source == "baseline"
    assert refinement_source is None
    assert model_reasons[-1] == "ensemble skipped: no persisted feature snapshot"


def test_edge_bps_with_resolution_risk_suppresses_high_risk_edges() -> None:
    risk = ResolutionRiskRow(
        condition_id="cond-1",
        risk_score=0.82,
        risk_level="high",
        is_flagged=True,
        risk_multiplier=1.66,
        classifier="heuristic_v1",
        reasons=["subjective threshold language"],
        event_time=datetime(2026, 4, 22, tzinfo=UTC),
        observed_at=datetime(2026, 4, 22, 12, tzinfo=UTC),
    )

    suppressed = _edge_bps_with_resolution_risk(
        model_prob=0.62,
        market_mid=0.51,
        resolution_risk=risk,
        suppress_threshold=0.75,
    )
    unsuppressed = _edge_bps_with_resolution_risk(
        model_prob=0.62,
        market_mid=0.51,
        resolution_risk=risk,
        suppress_threshold=0.9,
    )

    assert suppressed is None
    assert unsuppressed == pytest.approx(1100.0)


def test_apply_ensemble_refinement_downweights_adversarial_flow() -> None:
    classification = type("ClsRow", (), {"market_type": MarketType.THRESHOLD})()
    pipeline_result = PipelineResult(
        classification=ClassificationResult(
            market_type=MarketType.THRESHOLD,
            confidence=0.9,
            features=MarketFeatures(asset="BTC", strike=150_000, direction="above"),
            reasons=["threshold"],
        ),
        baseline=BaselineOutput(
            probability=0.42,
            source=BaselineSource.BS_ONE_TOUCH,
            reasons=["threshold baseline"],
        ),
        displayed_probability=0.42,
        displayed_source=BaselineSource.BS_ONE_TOUCH,
        _mid=0.39,
    )
    feature_snapshot = FeatureSnapshotRow(
        condition_id="cond-1",
        token_id="tok-yes",
        mid=0.39,
        spread=0.03,
        book_imbalance_1pct=0.2,
        book_imbalance_5pct=0.15,
        momentum_1h=0.02,
        momentum_24h=0.04,
        momentum_7d=0.06,
        realized_vol_24h=0.45,
        informed_taker_flow_24h=0.12,
        passive_maker_flow_24h=-0.03,
        decayed_directional_flow_24h=0.08,
        event_time=datetime(2026, 4, 22, tzinfo=UTC),
        observed_at=datetime(2026, 4, 22, 12, tzinfo=UTC),
    )

    model_prob, model_source, refinement_source, model_reasons = _apply_ensemble_refinement(
        registry=_ensemble_registry_payload(),
        classification=classification,
        pipeline_result=pipeline_result,
        market_mid=0.39,
        sibling_prior=None,
        feature_snapshot=feature_snapshot,
        smart_money=None,
        concentration=None,
        adversarial_flow=AdversarialFlowContext(
            condition_id="cond-1",
            score=0.8,
            is_flagged=True,
            thin_book=True,
            top_book_depth_usdc=350.0,
            flow_strength=0.55,
            external_divergence_count=1,
            external_event_count=0,
            reasons=["large directional flow"],
        ),
        concentration_threshold=0.6,
        adversarial_flow_blend_floor=0.25,
        asked_at=datetime(2026, 4, 22, 12, tzinfo=UTC),
    )

    assert model_source == "ensemble"
    assert refinement_source == "per_type_ensemble_v1"
    assert model_prob == pytest.approx(0.4025)
    assert model_reasons[-1] == "Adversarial-flow guard applied; ensemble delta scaled to 0.25"


def test_feature_attributions_for_sample_returns_ranked_driver_summaries() -> None:
    driver_summaries, attributions = _feature_attributions_for_sample(
        registry=_ensemble_registry_payload(),
        model_source="ensemble",
        sample=EnsembleSample(
            market_type=MarketType.THRESHOLD,
            p_base=0.7,
            market_mid=0.39,
            spread=0.03,
            book_imbalance_1pct=0.2,
            book_imbalance_5pct=0.15,
            momentum_1h=0.02,
            momentum_24h=0.04,
            momentum_7d=0.06,
            realized_vol_24h=0.45,
            informed_taker_flow_24h=0.12,
            passive_maker_flow_24h=-0.03,
            decayed_directional_flow_24h=0.08,
            asked_at=datetime(2026, 4, 22, 12, tzinfo=UTC).timestamp(),
        ),
    )

    assert driver_summaries
    assert "Baseline prior pushed the ensemble higher" in driver_summaries[0]
    assert attributions
    assert attributions[0]["feature_name"] == "p_base_logit"
    assert attributions[0]["label"] == "Baseline prior"
    assert attributions[0]["score_contribution"] > 0


def test_kelly_recommendation_returns_yes_and_no_sides() -> None:
    yes_side, yes_fraction, yes_uncapped = _kelly_recommendation(
        model_prob=0.62,
        market_mid=0.50,
        fractional_multiplier=0.25,
        cap=0.05,
    )
    no_side, no_fraction, no_uncapped = _kelly_recommendation(
        model_prob=0.34,
        market_mid=0.46,
        fractional_multiplier=0.25,
        cap=0.05,
    )

    assert yes_side == "YES"
    assert yes_uncapped == pytest.approx(0.24)
    assert yes_fraction == pytest.approx(0.05)
    assert no_side == "NO"
    assert no_uncapped == pytest.approx((0.46 - 0.34) / 0.46)
    assert no_fraction == pytest.approx(0.05)


def test_sample_history_rows_evenly_downsamples_series() -> None:
    rows = [
        QuoteRow(
            token_id="tok-yes",
            condition_id="cond-1",
            mid=0.4 + idx * 0.01,
            best_bid=0.39,
            best_ask=0.41,
            spread=0.02,
            event_time=datetime(2026, 4, 22, idx % 24, tzinfo=UTC),
            observed_at=datetime(2026, 4, 22, idx % 24, tzinfo=UTC),
        )
        for idx in range(12)
    ]

    sampled = _sample_history_rows(rows, max_points=4)

    assert len(sampled) == 4
    assert sampled[0] == rows[0]
    assert sampled[-1] == rows[-1]


@pytest.mark.asyncio
async def test_history_for_market_replays_model_at_sampled_points(monkeypatch: pytest.MonkeyPatch) -> None:
    asked_at = datetime(2026, 4, 22, 12, tzinfo=UTC)
    quote_rows = [
        QuoteRow(
            token_id="tok-yes",
            condition_id="cond-1",
            mid=0.40 + idx * 0.05,
            best_bid=0.39,
            best_ask=0.41,
            spread=0.02,
            event_time=datetime(2026, 4, 22, idx, tzinfo=UTC),
            observed_at=datetime(2026, 4, 22, idx, tzinfo=UTC),
        )
        for idx in range(3)
    ]

    async def fake_snapshot(ch, condition_id, asked_at):
        return MarketSnapshotRow(
            condition_id=condition_id,
            question="Will BTC rise?",
            description="",
            resolution_source="",
            active=True,
            closed=False,
            volume_usdc=1000.0,
            liquidity_usdc=500.0,
            open_interest_usdc=400.0,
            end_date=None,
            token_ids=["tok-yes"],
            event_time=asked_at,
            observed_at=asked_at,
        )

    async def fake_quotes_pit(ch, token_id, start, end):
        return quote_rows

    async def fake_model_for_market(ch, *, condition_id, asked_at, tuning_profile=None):
        del tuning_profile
        idx = next(i for i, row in enumerate(quote_rows) if row.event_time == asked_at)
        return type("Detail", (), {"model_prob": 0.45 + idx * 0.05})()

    monkeypatch.setattr("api.markets.asof_q.latest_market_snapshot_asof", fake_snapshot)
    monkeypatch.setattr("api.markets.asof_q.quotes_timeseries_pit", fake_quotes_pit)
    monkeypatch.setattr("api.markets.model_for_market", fake_model_for_market)

    history = await history_for_market(
        ch=object(),
        condition_id="cond-1",
        asked_at=asked_at,
        window_hours=24,
        max_points=16,
    )

    assert [point.market_mid for point in history] == [0.40, 0.45, 0.50]
    assert [point.model_prob for point in history] == [0.45, 0.50, 0.55]


def test_apply_conformal_interval_uses_mondrian_registry_and_multipliers() -> None:
    classification = type("ClsRow", (), {"market_type": MarketType.THRESHOLD})()
    registry = SplitConformalRegistry(
        coverage=0.8,
        cells={
            "threshold:1d_7d": ConformalCell(
                key="threshold:1d_7d",
                quantile=0.1,
                sample_count=20,
            )
        },
    )

    band_lo, band_hi, band_coverage, uncertainty_source = _apply_conformal_interval(
        registry=registry,
        classification=classification,
        model_prob=0.55,
        time_to_resolution_s=2 * 24 * 3600,
        uncertainty_multiplier=1.5,
        resolution_risk_multiplier=1.2,
    )

    assert band_lo == pytest.approx(0.37)
    assert band_hi == pytest.approx(0.73)
    assert band_coverage == pytest.approx(0.8)
    assert uncertainty_source == "split_conformal_mondrian"
