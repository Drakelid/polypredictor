"""Tests for the M6.3 auto-disable serve-time gate.

When ``model_disable_log`` says a market type is in ``disabled`` state, the
ensemble refinement MUST short-circuit and return the baseline path —
regardless of whether a registry has a model for that type. The drift driver
flips back to ``re_enabled`` to restore ensemble service.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from api.markets import _apply_ensemble_refinement
from api.model_status import ModelDisableStatus
from model import (
    BOOSTER_FEATURE_NAMES,
    BaselineOutput,
    BaselineSource,
    ClassificationResult,
    EnsembleRegistry,
    MarketFeatures,
    MarketType,
    PipelineResult,
)


def _registry_with_threshold_model() -> EnsembleRegistry:
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
                    "linear_intercept": 0.0,
                    "linear_weights": {"p_base_logit": 1.0, "market_mid_logit": 0.0},
                    "stumps": [],
                    "calibrator": {
                        "upper_bounds": [1.0],
                        "values": [0.5],
                    },
                    "market_mid_weight_cap": 0.5,
                }
            }
        }
    )


def _classification() -> object:
    return type("ClsRow", (), {"market_type": MarketType.THRESHOLD})()


def _pipeline_result() -> PipelineResult:
    return PipelineResult(
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


def _disable_status(*, is_disabled: bool) -> ModelDisableStatus:
    observed = datetime(2026, 4, 24, 12, tzinfo=UTC)
    return ModelDisableStatus(
        market_type="threshold",
        is_disabled=is_disabled,
        action="disabled" if is_disabled else "re_enabled",
        consecutive_days=8 if is_disabled else 0,
        threshold_days=7,
        reason=(
            "8 consecutive days of negative 30d Brier skill"
            if is_disabled
            else "skill recovered above baseline"
        ),
        event_time=observed,
        observed_at=observed,
    )


def test_disabled_status_short_circuits_to_baseline_disabled() -> None:
    model_prob, model_source, refinement_source, model_reasons = (
        _apply_ensemble_refinement(
            registry=_registry_with_threshold_model(),
            classification=_classification(),
            pipeline_result=_pipeline_result(),
            market_mid=0.39,
            sibling_prior=None,
            feature_snapshot=None,
            smart_money=None,
            concentration=None,
            adversarial_flow=None,
            disable_status=_disable_status(is_disabled=True),
            concentration_threshold=0.6,
            adversarial_flow_blend_floor=0.25,
            asked_at=datetime(2026, 4, 24, 12, tzinfo=UTC),
        )
    )

    assert model_source == "baseline_disabled"
    assert refinement_source is None
    # Displayed probability falls through to baseline (0.42).
    assert model_prob == pytest.approx(0.42)
    # Reasons append the disable explanation so the UI can render it.
    assert any("auto-disabled" in r for r in model_reasons)
    assert any("8 consecutive days" in r for r in model_reasons)


def test_re_enabled_status_does_not_short_circuit() -> None:
    # is_disabled=False → the gate is open, the ensemble registry is consulted
    # as normal. Without a feature_snapshot we still degrade to baseline, but
    # the source label should be 'baseline' not 'baseline_disabled'.
    model_prob, model_source, refinement_source, model_reasons = (
        _apply_ensemble_refinement(
            registry=_registry_with_threshold_model(),
            classification=_classification(),
            pipeline_result=_pipeline_result(),
            market_mid=0.39,
            sibling_prior=None,
            feature_snapshot=None,
            smart_money=None,
            concentration=None,
            adversarial_flow=None,
            disable_status=_disable_status(is_disabled=False),
            concentration_threshold=0.6,
            adversarial_flow_blend_floor=0.25,
            asked_at=datetime(2026, 4, 24, 12, tzinfo=UTC),
        )
    )
    assert model_source != "baseline_disabled"
    assert not any("auto-disabled" in r for r in model_reasons)


def test_no_disable_status_means_enabled_default() -> None:
    # disable_status=None → market_type has never been touched by the rule;
    # treated as enabled. Same outcome as re_enabled, no short-circuit.
    model_prob, model_source, refinement_source, model_reasons = (
        _apply_ensemble_refinement(
            registry=_registry_with_threshold_model(),
            classification=_classification(),
            pipeline_result=_pipeline_result(),
            market_mid=0.39,
            sibling_prior=None,
            feature_snapshot=None,
            smart_money=None,
            concentration=None,
            adversarial_flow=None,
            disable_status=None,
            concentration_threshold=0.6,
            adversarial_flow_blend_floor=0.25,
            asked_at=datetime(2026, 4, 24, 12, tzinfo=UTC),
        )
    )
    assert model_source != "baseline_disabled"
