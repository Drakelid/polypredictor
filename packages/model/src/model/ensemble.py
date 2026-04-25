"""Per-type stacked ensemble for M2.

The design is intentionally lightweight and dependency-stable:

* a bounded linear logit stack over ``p_base`` and ``market_mid``
* gradient-boosted decision stumps over persisted microstructure features
* a per-type isotonic calibrator fit on the latest held-out time slice

This keeps the model package self-contained while matching the M2.2 contract:
consume ``p_base`` + microstructure features, emit ``p_raw``, calibrate per
market type, and cap direct dependence on ``market_mid``.
"""

from __future__ import annotations

import json
import math
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit

from .types import MarketType

_EPS = 1e-6

LINEAR_FEATURE_NAMES = (
    "p_base_logit",
    "market_mid_logit",
    "sibling_implied_prior_logit",
)
BOOSTER_FEATURE_NAMES = (
    "spread",
    "book_imbalance_1pct",
    "book_imbalance_5pct",
    "momentum_1h",
    "momentum_24h",
    "momentum_7d",
    "realized_vol_24h",
    "informed_taker_flow_24h",
    "passive_maker_flow_24h",
    "decayed_directional_flow_24h",
    # M3.1 smart-money + M3.2 concentration features. The PRD calls for
    # down-weighting smart_money_consensus when concentration > 0.6; we apply
    # that in the API layer (services/api/src/api/markets.py) before building
    # the sample, so the model sees a pre-attenuated consensus value and the
    # linear stack does not have to learn the interaction.
    "smart_money_consensus",
    "holder_concentration",
    "resolution_risk_score",
    "adversarial_flow_score",
    "regime_bull_trend",
    "regime_bear_trend",
    "regime_chop",
    "regime_liquidity_crisis",
    # M5 onchain — z-scores / pct deltas, see services/api/src/api/onchain_features.py.
    # Legacy registries that pre-date these names train no stumps for them, so
    # serve-time predictions on older registries are unaffected.
    "onchain_exchange_inflow_z",
    "onchain_exchange_outflow_z",
    "onchain_whale_count_delta_pct",
    "onchain_stablecoin_supply_delta_pct",
    # M5 macro — FRED-derived scalars, see services/api/src/api/macro_features.py.
    # Asset-agnostic; same value applies to every market in a request.
    "macro_fed_funds_30d_delta",
    "macro_treasury_10y_30d_delta",
    "macro_yield_curve_2s10s",
    "macro_cpi_yoy_pct",
)

REGIME_FEATURE_NAMES = (
    "regime_bull_trend",
    "regime_bear_trend",
    "regime_chop",
    "regime_liquidity_crisis",
)


@dataclass(frozen=True)
class EnsembleSample:
    market_type: MarketType
    p_base: float
    market_mid: float | None = None
    sibling_implied_prior: float | None = None
    spread: float | None = None
    book_imbalance_1pct: float | None = None
    book_imbalance_5pct: float | None = None
    momentum_1h: float | None = None
    momentum_24h: float | None = None
    momentum_7d: float | None = None
    realized_vol_24h: float | None = None
    informed_taker_flow_24h: float | None = None
    passive_maker_flow_24h: float | None = None
    decayed_directional_flow_24h: float | None = None
    smart_money_consensus: float | None = None
    holder_concentration: float | None = None
    resolution_risk_score: float | None = None
    adversarial_flow_score: float | None = None
    regime_label: str | None = None
    onchain_exchange_inflow_z: float | None = None
    onchain_exchange_outflow_z: float | None = None
    onchain_whale_count_delta_pct: float | None = None
    onchain_stablecoin_supply_delta_pct: float | None = None
    macro_fed_funds_30d_delta: float | None = None
    macro_treasury_10y_30d_delta: float | None = None
    macro_yield_curve_2s10s: float | None = None
    macro_cpi_yoy_pct: float | None = None
    outcome: int | None = None
    asked_at: float = 0.0


@dataclass(frozen=True)
class FeatureContribution:
    feature_name: str
    feature_value: float | None
    transformed_value: float
    score_contribution: float


@dataclass(frozen=True)
class PredictionExplanation:
    raw_score: float
    raw_probability: float
    calibrated_probability: float
    contributions: list[FeatureContribution]


@dataclass(frozen=True)
class GradientBoostStump:
    feature_name: str
    threshold: float
    left_value: float
    right_value: float

    def contribution(self, value: float) -> float:
        return self.left_value if value <= self.threshold else self.right_value

    def to_dict(self) -> dict[str, object]:
        return {
            "feature_name": self.feature_name,
            "threshold": self.threshold,
            "left_value": self.left_value,
            "right_value": self.right_value,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> GradientBoostStump:
        return cls(
            feature_name=str(payload["feature_name"]),
            threshold=float(payload["threshold"]),
            left_value=float(payload["left_value"]),
            right_value=float(payload["right_value"]),
        )


@dataclass(frozen=True)
class IsotonicCalibrator:
    upper_bounds: list[float]
    values: list[float]

    @classmethod
    def fit(
        cls,
        probabilities: list[float],
        outcomes: list[int],
    ) -> IsotonicCalibrator:
        if not probabilities or len(probabilities) != len(outcomes):
            return cls(upper_bounds=[1.0], values=[0.5])
        pairs = sorted(
            (_clip_probability(probability), int(outcome))
             for probability, outcome in zip(probabilities, outcomes, strict=True)
        )
        blocks: list[dict[str, float]] = [
            {
                "upper": probability,
                "sum_y": float(outcome),
                "count": 1.0,
            }
            for probability, outcome in pairs
        ]
        idx = 0
        while idx < len(blocks) - 1:
            left_mean = blocks[idx]["sum_y"] / blocks[idx]["count"]
            right_mean = blocks[idx + 1]["sum_y"] / blocks[idx + 1]["count"]
            if left_mean <= right_mean:
                idx += 1
                continue
            blocks[idx]["upper"] = blocks[idx + 1]["upper"]
            blocks[idx]["sum_y"] += blocks[idx + 1]["sum_y"]
            blocks[idx]["count"] += blocks[idx + 1]["count"]
            del blocks[idx + 1]
            idx = max(0, idx - 1)
        return cls(
            upper_bounds=[float(block["upper"]) for block in blocks],
            values=[
                float(block["sum_y"] / block["count"])
                for block in blocks
            ],
        )

    def predict(self, probability: float) -> float:
        clipped = _clip_probability(probability)
        idx = bisect_right(self.upper_bounds, clipped)
        if idx >= len(self.values):
            return float(self.values[-1])
        return float(self.values[idx])

    def to_dict(self) -> dict[str, object]:
        return {
            "upper_bounds": list(self.upper_bounds),
            "values": list(self.values),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> IsotonicCalibrator:
        upper_bounds_raw = payload.get("upper_bounds", [])
        values_raw = payload.get("values", [])
        upper_bounds = [float(value) for value in upper_bounds_raw] if isinstance(upper_bounds_raw, list) else [1.0]
        values = [float(value) for value in values_raw] if isinstance(values_raw, list) else [0.5]
        if not upper_bounds or not values:
            return cls(upper_bounds=[1.0], values=[0.5])
        return cls(upper_bounds=upper_bounds, values=values)


@dataclass(frozen=True)
class TypeEnsembleModel:
    market_type: MarketType
    linear_means: dict[str, float]
    linear_scales: dict[str, float]
    booster_means: dict[str, float]
    booster_scales: dict[str, float]
    linear_intercept: float
    linear_weights: dict[str, float]
    stumps: list[GradientBoostStump]
    calibrator: IsotonicCalibrator
    market_mid_weight_cap: float

    def predict_raw_probability(self, sample: EnsembleSample) -> float:
        score = self.linear_intercept
        for feature_name in LINEAR_FEATURE_NAMES:
            score += self.linear_weights.get(feature_name, 0.0) * _scaled_value(
                sample,
                feature_name=feature_name,
                means=self.linear_means,
                scales=self.linear_scales,
            )
        for stump in self.stumps:
            score += stump.contribution(
                _scaled_value(
                    sample,
                    feature_name=stump.feature_name,
                    means=self.booster_means,
                    scales=self.booster_scales,
                )
            )
        return float(expit(score))

    def predict_probability(self, sample: EnsembleSample) -> float:
        raw = self.predict_raw_probability(sample)
        return self.calibrator.predict(raw)

    def explain_prediction(self, sample: EnsembleSample) -> PredictionExplanation:
        score = self.linear_intercept
        contributions = [
            FeatureContribution(
                feature_name="intercept",
                feature_value=None,
                transformed_value=1.0,
                score_contribution=self.linear_intercept,
            )
        ]
        for feature_name in LINEAR_FEATURE_NAMES:
            transformed_value = _scaled_value(
                sample,
                feature_name=feature_name,
                means=self.linear_means,
                scales=self.linear_scales,
            )
            weight = self.linear_weights.get(feature_name, 0.0)
            contribution = weight * transformed_value
            score += contribution
            contributions.append(
                FeatureContribution(
                    feature_name=feature_name,
                    feature_value=_feature_value(sample, feature_name),
                    transformed_value=transformed_value,
                    score_contribution=contribution,
                )
            )
        for stump in self.stumps:
            transformed_value = _scaled_value(
                sample,
                feature_name=stump.feature_name,
                means=self.booster_means,
                scales=self.booster_scales,
            )
            contribution = stump.contribution(transformed_value)
            score += contribution
            contributions.append(
                FeatureContribution(
                    feature_name=stump.feature_name,
                    feature_value=_feature_value(sample, stump.feature_name),
                    transformed_value=transformed_value,
                    score_contribution=contribution,
                )
            )
        raw_probability = float(expit(score))
        calibrated_probability = self.calibrator.predict(raw_probability)
        ranked = sorted(
            contributions,
            key=lambda item: abs(item.score_contribution),
            reverse=True,
        )
        return PredictionExplanation(
            raw_score=score,
            raw_probability=raw_probability,
            calibrated_probability=calibrated_probability,
            contributions=ranked,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "market_type": self.market_type.value,
            "linear_means": dict(self.linear_means),
            "linear_scales": dict(self.linear_scales),
            "booster_means": dict(self.booster_means),
            "booster_scales": dict(self.booster_scales),
            "linear_intercept": self.linear_intercept,
            "linear_weights": dict(self.linear_weights),
            "stumps": [stump.to_dict() for stump in self.stumps],
            "calibrator": self.calibrator.to_dict(),
            "market_mid_weight_cap": self.market_mid_weight_cap,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> TypeEnsembleModel:
        return cls(
            market_type=MarketType(str(payload["market_type"])),
            linear_means=_float_dict(payload.get("linear_means")),
            linear_scales=_float_dict(payload.get("linear_scales"), default=1.0),
            booster_means=_float_dict(payload.get("booster_means")),
            booster_scales=_float_dict(payload.get("booster_scales"), default=1.0),
            linear_intercept=float(payload.get("linear_intercept", 0.0)),
            linear_weights=_float_dict(payload.get("linear_weights")),
            stumps=[
                GradientBoostStump.from_dict(item)
                for item in payload.get("stumps", [])
                if isinstance(item, dict)
            ],
            calibrator=IsotonicCalibrator.from_dict(
                payload.get("calibrator", {}) if isinstance(payload.get("calibrator"), dict) else {}
            ),
            market_mid_weight_cap=float(payload.get("market_mid_weight_cap", 0.0)),
        )


@dataclass(frozen=True)
class EnsembleRegistry:
    models: dict[MarketType, TypeEnsembleModel]

    def model_for_type(self, market_type: MarketType) -> TypeEnsembleModel | None:
        return self.models.get(market_type)

    def predict_probability(self, sample: EnsembleSample) -> float | None:
        model = self.model_for_type(sample.market_type)
        if model is None:
            return None
        return model.predict_probability(sample)

    def predict_raw_probability(self, sample: EnsembleSample) -> float | None:
        model = self.model_for_type(sample.market_type)
        if model is None:
            return None
        return model.predict_raw_probability(sample)

    def explain_prediction(self, sample: EnsembleSample) -> PredictionExplanation | None:
        model = self.model_for_type(sample.market_type)
        if model is None:
            return None
        return model.explain_prediction(sample)

    def to_dict(self) -> dict[str, object]:
        return {
            "models": {
                market_type.value: model.to_dict()
                for market_type, model in sorted(self.models.items(), key=lambda item: item[0].value)
            }
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> EnsembleRegistry:
        raw_models = payload.get("models", {})
        models: dict[MarketType, TypeEnsembleModel] = {}
        if isinstance(raw_models, dict):
            for market_type, model_payload in raw_models.items():
                if not isinstance(model_payload, dict):
                    continue
                parsed_type = MarketType(str(market_type))
                models[parsed_type] = TypeEnsembleModel.from_dict(model_payload)
        return cls(models=models)

    @classmethod
    def from_json(cls, payload: str) -> EnsembleRegistry:
        parsed = json.loads(payload)
        if not isinstance(parsed, dict):
            return cls(models={})
        return cls.from_dict(parsed)


def fit_per_type_ensembles(
    samples: list[EnsembleSample],
    *,
    market_mid_weight_cap: float = 0.35,
    l2: float = 0.05,
    calibration_fraction: float = 0.2,
    min_calibration_size: int = 12,
    n_estimators: int = 25,
    learning_rate: float = 0.1,
    min_samples_leaf: int = 8,
) -> EnsembleRegistry:
    grouped: dict[MarketType, list[EnsembleSample]] = defaultdict(list)
    for sample in samples:
        if sample.outcome is None:
            continue
        grouped[sample.market_type].append(sample)
    models: dict[MarketType, TypeEnsembleModel] = {}
    for market_type, group in grouped.items():
        if len(group) < 8:
            continue
        ordered = sorted(group, key=lambda sample: sample.asked_at)
        split_idx = round(len(ordered) * (1.0 - calibration_fraction))
        if split_idx < max(4, len(ordered) - min_calibration_size):
            split_idx = max(4, len(ordered) - min_calibration_size)
        if split_idx >= len(ordered):
            split_idx = len(ordered) - 1
        train_samples = ordered[:split_idx]
        calibration_samples = ordered[split_idx:]
        if len(calibration_samples) < min_calibration_size:
            train_samples = ordered
            calibration_samples = ordered
        models[market_type] = _fit_type_model(
            market_type,
            train_samples=train_samples,
            calibration_samples=calibration_samples,
            market_mid_weight_cap=market_mid_weight_cap,
            l2=l2,
            n_estimators=n_estimators,
            learning_rate=learning_rate,
            min_samples_leaf=min_samples_leaf,
        )
    return EnsembleRegistry(models=models)


def _fit_type_model(
    market_type: MarketType,
    *,
    train_samples: list[EnsembleSample],
    calibration_samples: list[EnsembleSample],
    market_mid_weight_cap: float,
    l2: float,
    n_estimators: int,
    learning_rate: float,
    min_samples_leaf: int,
) -> TypeEnsembleModel:
    linear_means, linear_scales = _fit_stats(train_samples, LINEAR_FEATURE_NAMES)
    booster_means, booster_scales = _fit_stats(train_samples, BOOSTER_FEATURE_NAMES)
    x_linear = _matrix(train_samples, LINEAR_FEATURE_NAMES, linear_means, linear_scales)
    y = np.array([int(sample.outcome or 0) for sample in train_samples], dtype=float)
    intercept, weights = _fit_bounded_logit(
        x_linear,
        y,
        market_mid_index=1,
        market_mid_weight_cap=market_mid_weight_cap,
        l2=l2,
    )
    raw_scores = intercept + x_linear @ weights

    x_boost = _matrix(train_samples, BOOSTER_FEATURE_NAMES, booster_means, booster_scales)
    stumps: list[GradientBoostStump] = []
    for _ in range(max(0, n_estimators)):
        residual = y - expit(raw_scores)
        stump = _fit_best_stump(
            x_boost,
            residual,
            feature_names=BOOSTER_FEATURE_NAMES,
            learning_rate=learning_rate,
            min_samples_leaf=min_samples_leaf,
        )
        if stump is None:
            break
        stumps.append(stump)
        raw_scores += np.array(
            [stump.contribution(float(value)) for value in x_boost[:, BOOSTER_FEATURE_NAMES.index(stump.feature_name)]],
            dtype=float,
        )

    raw_calibration = [
        _predict_raw_probability(
            sample,
            linear_intercept=intercept,
            linear_weights=weights,
            linear_means=linear_means,
            linear_scales=linear_scales,
            booster_means=booster_means,
            booster_scales=booster_scales,
            stumps=stumps,
        )
        for sample in calibration_samples
    ]
    calibrator = IsotonicCalibrator.fit(
        raw_calibration,
        [int(sample.outcome or 0) for sample in calibration_samples],
    )
    return TypeEnsembleModel(
        market_type=market_type,
        linear_means=linear_means,
        linear_scales=linear_scales,
        booster_means=booster_means,
        booster_scales=booster_scales,
        linear_intercept=float(intercept),
        linear_weights={
            name: float(weight) for name, weight in zip(LINEAR_FEATURE_NAMES, weights, strict=True)
        },
        stumps=stumps,
        calibrator=calibrator,
        market_mid_weight_cap=market_mid_weight_cap,
    )


def _fit_bounded_logit(
    x: np.ndarray,
    y: np.ndarray,
    *,
    market_mid_index: int,
    market_mid_weight_cap: float,
    l2: float,
) -> tuple[float, np.ndarray]:
    def objective(params: np.ndarray) -> tuple[float, np.ndarray]:
        intercept = float(params[0])
        weights = params[1:]
        score = intercept + x @ weights
        probability = expit(score)
        loss = float(np.sum(np.logaddexp(0.0, score) - y * score))
        loss += 0.5 * l2 * float(np.sum(weights * weights))
        grad_intercept = float(np.sum(probability - y))
        grad_weights = x.T @ (probability - y) + l2 * weights
        gradient = np.concatenate(([grad_intercept], grad_weights))
        return loss, gradient

    initial = np.zeros(x.shape[1] + 1, dtype=float)
    bounds: list[tuple[float | None, float | None]] = [(None, None)] * (x.shape[1] + 1)
    bounds[1 + market_mid_index] = (-market_mid_weight_cap, market_mid_weight_cap)
    result = minimize(
        lambda params: objective(params)[0],
        initial,
        method="L-BFGS-B",
        jac=lambda params: objective(params)[1],
        bounds=bounds,
    )
    params = result.x if result.success else initial
    return float(params[0]), params[1:]


def _fit_best_stump(
    x: np.ndarray,
    residual: np.ndarray,
    *,
    feature_names: tuple[str, ...],
    learning_rate: float,
    min_samples_leaf: int,
) -> GradientBoostStump | None:
    best_feature: str | None = None
    best_threshold = 0.0
    best_left = 0.0
    best_right = 0.0
    best_error: float | None = None

    for feature_idx, feature_name in enumerate(feature_names):
        values = x[:, feature_idx]
        unique = np.unique(values)
        if unique.size < 2:
            continue
        thresholds = (unique[:-1] + unique[1:]) / 2.0
        for threshold in thresholds:
            left_mask = values <= threshold
            right_mask = ~left_mask
            if int(np.sum(left_mask)) < min_samples_leaf or int(np.sum(right_mask)) < min_samples_leaf:
                continue
            left_value = float(np.mean(residual[left_mask]))
            right_value = float(np.mean(residual[right_mask]))
            prediction = np.where(left_mask, left_value, right_value)
            error = float(np.sum((residual - prediction) ** 2))
            if best_error is not None and error >= best_error:
                continue
            best_error = error
            best_feature = feature_name
            best_threshold = float(threshold)
            best_left = left_value * learning_rate
            best_right = right_value * learning_rate

    if best_feature is None:
        return None
    if abs(best_left) < 1e-9 and abs(best_right) < 1e-9:
        return None
    return GradientBoostStump(
        feature_name=best_feature,
        threshold=best_threshold,
        left_value=best_left,
        right_value=best_right,
    )


def _fit_stats(
    samples: list[EnsembleSample],
    feature_names: tuple[str, ...],
) -> tuple[dict[str, float], dict[str, float]]:
    means: dict[str, float] = {}
    scales: dict[str, float] = {}
    for feature_name in feature_names:
        values = [
            value
            for sample in samples
            if (value := _raw_feature_value(sample, feature_name)) is not None
        ]
        if values:
            mean = float(np.mean(values))
            scale = float(np.std(values))
            means[feature_name] = mean
            scales[feature_name] = scale if scale > 1e-8 else 1.0
        else:
            means[feature_name] = 0.0
            scales[feature_name] = 1.0
    return means, scales


def _matrix(
    samples: list[EnsembleSample],
    feature_names: tuple[str, ...],
    means: dict[str, float],
    scales: dict[str, float],
) -> np.ndarray:
    return np.array(
        [
            [
                _scaled_value(sample, feature_name=feature_name, means=means, scales=scales)
                for feature_name in feature_names
            ]
            for sample in samples
        ],
        dtype=float,
    )


def _predict_raw_probability(
    sample: EnsembleSample,
    *,
    linear_intercept: float,
    linear_weights: np.ndarray,
    linear_means: dict[str, float],
    linear_scales: dict[str, float],
    booster_means: dict[str, float],
    booster_scales: dict[str, float],
    stumps: list[GradientBoostStump],
) -> float:
    score = linear_intercept
    for feature_name, weight in zip(LINEAR_FEATURE_NAMES, linear_weights, strict=True):
        score += float(weight) * _scaled_value(
            sample,
            feature_name=feature_name,
            means=linear_means,
            scales=linear_scales,
        )
    for stump in stumps:
        score += stump.contribution(
            _scaled_value(
                sample,
                feature_name=stump.feature_name,
                means=booster_means,
                scales=booster_scales,
            )
        )
    return float(expit(score))


def _feature_value(sample: EnsembleSample, feature_name: str) -> float | None:
    if feature_name == "p_base_logit":
        return float(sample.p_base)
    if feature_name == "market_mid_logit":
        return float(sample.market_mid) if sample.market_mid is not None else None
    if feature_name == "sibling_implied_prior_logit":
        return (
            float(sample.sibling_implied_prior)
            if sample.sibling_implied_prior is not None
            else None
        )
    if feature_name in REGIME_FEATURE_NAMES:
        return _regime_indicator(sample.regime_label, feature_name)
    value = getattr(sample, feature_name)
    return float(value) if value is not None else None


def _raw_feature_value(sample: EnsembleSample, feature_name: str) -> float | None:
    if feature_name == "p_base_logit":
        return _safe_logit(sample.p_base)
    if feature_name == "market_mid_logit":
        return _safe_logit(sample.market_mid)
    if feature_name == "sibling_implied_prior_logit":
        return _safe_logit(sample.sibling_implied_prior)
    if feature_name in REGIME_FEATURE_NAMES:
        return _regime_indicator(sample.regime_label, feature_name)
    value = getattr(sample, feature_name)
    return float(value) if value is not None else None


def _scaled_value(
    sample: EnsembleSample,
    *,
    feature_name: str,
    means: dict[str, float],
    scales: dict[str, float],
) -> float:
    raw = _raw_feature_value(sample, feature_name)
    mean = means.get(feature_name, 0.0)
    scale = scales.get(feature_name, 1.0)
    if raw is None:
        raw = mean
    return float((raw - mean) / scale)


def _clip_probability(probability: float) -> float:
    return min(max(float(probability), _EPS), 1.0 - _EPS)


def _safe_logit(probability: float | None) -> float | None:
    if probability is None:
        return None
    clipped = _clip_probability(probability)
    return math.log(clipped / (1.0 - clipped))


def _regime_indicator(regime_label: str | None, feature_name: str) -> float:
    expected = feature_name.removeprefix("regime_")
    return 1.0 if regime_label == expected else 0.0


def _float_dict(payload: object, *, default: float = 0.0) -> dict[str, float]:
    if not isinstance(payload, dict):
        return {}
    out: dict[str, float] = {}
    for key, value in payload.items():
        out[str(key)] = float(value) if value is not None else default
    return out
