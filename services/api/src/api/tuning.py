"""User tuning profiles for M7.2.

The user-facing contract is intentionally narrow:

* tuning only applies an additive shift in log-odds space
* no learned ensemble or baseline weights are modified
* a single active profile drives live API serving for the demo user
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime

from asyncpg import Pool

from .settings import Settings
from .users import ensure_demo_user

SUPPORTED_SHIFT_KEYS = (
    "smart_money",
    "sibling_prior",
    "holder_concentration",
    "resolution_risk",
    "adversarial_flow",
)
SUPPORTED_PRESETS = ("conservative", "balanced", "aggressive", "custom")
_MAX_ABS_SHIFT = 1.0

PRESET_LOG_ODDS_SHIFTS: dict[str, dict[str, float]] = {
    "conservative": {
        "smart_money": 0.10,
        "sibling_prior": 0.10,
        "holder_concentration": 0.25,
        "resolution_risk": 0.45,
        "adversarial_flow": 0.35,
    },
    "balanced": {
        "smart_money": 0.0,
        "sibling_prior": 0.0,
        "holder_concentration": 0.0,
        "resolution_risk": 0.0,
        "adversarial_flow": 0.0,
    },
    "aggressive": {
        "smart_money": 0.25,
        "sibling_prior": 0.20,
        "holder_concentration": -0.10,
        "resolution_risk": -0.20,
        "adversarial_flow": -0.15,
    },
}
PRESET_LABELS = {
    "conservative": "Conservative",
    "balanced": "Balanced",
    "aggressive": "Aggressive",
    "custom": "Custom",
}


@dataclass(frozen=True)
class TuningProfile:
    name: str
    preset: str
    log_odds_shifts: dict[str, float]
    is_active: bool
    updated_at: datetime | None


@dataclass(frozen=True)
class TuningProfileInput:
    preset: str
    log_odds_shifts: dict[str, float]


@dataclass(frozen=True)
class TuningAdjustmentContext:
    model_prob: float | None
    smart_money_consensus: float | None = None
    smart_money_dominant: str | None = None
    sibling_implied_prior: float | None = None
    concentration_score: float | None = None
    resolution_risk_score: float | None = None
    adversarial_flow_score: float | None = None


@dataclass(frozen=True)
class TuningAdjustment:
    tuned_probability: float | None
    total_log_odds_shift: float
    component_shifts: dict[str, float]
    reasons: list[str]


async def get_active_profile(
    *,
    pool: Pool,
    settings: Settings,
) -> TuningProfile:
    user_id = await ensure_demo_user(pool, settings)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT name, preset, log_odds_shifts, is_active, updated_at
            FROM tuning_profiles
            WHERE user_id = $1
              AND is_active = TRUE
            ORDER BY updated_at DESC, created_at DESC
            LIMIT 1
            """,
            user_id,
        )
    if row is None:
        return TuningProfile(
            name="Balanced",
            preset="balanced",
            log_odds_shifts=_preset_shifts("balanced"),
            is_active=True,
            updated_at=None,
        )
    return TuningProfile(
        name=str(row["name"]),
        preset=str(row["preset"] or "custom"),
        log_odds_shifts=_normalize_log_odds_shifts(row["log_odds_shifts"] or {}),
        is_active=bool(row["is_active"]),
        updated_at=_coerce_utc(row["updated_at"]) if row["updated_at"] is not None else None,
    )


async def update_active_profile(
    *,
    pool: Pool,
    settings: Settings,
    payload: TuningProfileInput,
) -> TuningProfile:
    user_id = await ensure_demo_user(pool, settings)
    shifts = _resolve_input_shifts(payload)
    preset = _match_preset(shifts) or "custom"
    name = PRESET_LABELS[preset]
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE tuning_profiles
            SET is_active = FALSE
            WHERE user_id = $1
            """,
            user_id,
        )
        row = await conn.fetchrow(
            """
            INSERT INTO tuning_profiles (
                user_id,
                name,
                preset,
                log_odds_shifts,
                is_active
            )
            VALUES ($1, $2, $3, $4, TRUE)
            ON CONFLICT (user_id, name) DO UPDATE
            SET preset = EXCLUDED.preset,
                log_odds_shifts = EXCLUDED.log_odds_shifts,
                is_active = TRUE,
                updated_at = NOW()
            RETURNING name, preset, log_odds_shifts, is_active, updated_at
            """,
            user_id,
            name,
            preset,
            shifts,
        )
    assert row is not None
    return TuningProfile(
        name=str(row["name"]),
        preset=str(row["preset"]),
        log_odds_shifts=_normalize_log_odds_shifts(row["log_odds_shifts"] or {}),
        is_active=bool(row["is_active"]),
        updated_at=_coerce_utc(row["updated_at"]) if row["updated_at"] is not None else None,
    )


def apply_tuning_adjustment(
    *,
    profile: TuningProfile | None,
    context: TuningAdjustmentContext,
    concentration_threshold: float,
) -> TuningAdjustment:
    model_prob = context.model_prob
    if model_prob is None or profile is None:
        return TuningAdjustment(
            tuned_probability=model_prob,
            total_log_odds_shift=0.0,
            component_shifts={},
            reasons=[],
        )
    shifts = _normalize_log_odds_shifts(profile.log_odds_shifts)
    if all(abs(value) < 1e-9 for value in shifts.values()):
        return TuningAdjustment(
            tuned_probability=model_prob,
            total_log_odds_shift=0.0,
            component_shifts={},
            reasons=[],
        )
    directional = {
        "smart_money": _smart_money_signal(
            context.smart_money_consensus,
            context.smart_money_dominant,
        ),
        "sibling_prior": _sibling_signal(context.sibling_implied_prior),
        "holder_concentration": _neutral_caution_signal(
            model_prob,
            context.concentration_score,
            threshold=concentration_threshold,
        ),
        "resolution_risk": _neutral_caution_signal(
            model_prob,
            context.resolution_risk_score,
            threshold=0.0,
        ),
        "adversarial_flow": _neutral_caution_signal(
            model_prob,
            context.adversarial_flow_score,
            threshold=0.0,
        ),
    }
    component_shifts = {
        key: shifts.get(key, 0.0) * directional.get(key, 0.0)
        for key in SUPPORTED_SHIFT_KEYS
        if abs(shifts.get(key, 0.0)) > 1e-9 and abs(directional.get(key, 0.0)) > 1e-9
    }
    total_shift = sum(component_shifts.values())
    if abs(total_shift) < 1e-9:
        return TuningAdjustment(
            tuned_probability=model_prob,
            total_log_odds_shift=0.0,
            component_shifts={},
            reasons=[],
        )
    tuned_probability = _sigmoid(_logit(model_prob) + total_shift)
    reasons = [
        (
            f"User tuning ({profile.name}) applied {total_shift:+.3f} log-odds "
            f"from {key.replace('_', ' ')}"
        )
        for key, value in sorted(
            component_shifts.items(),
            key=lambda item: abs(item[1]),
            reverse=True,
        )[:2]
        if abs(value) >= 0.02
    ]
    if not reasons:
        reasons = [f"User tuning ({profile.name}) applied {total_shift:+.3f} log-odds"]
    return TuningAdjustment(
        tuned_probability=tuned_probability,
        total_log_odds_shift=total_shift,
        component_shifts=component_shifts,
        reasons=reasons,
    )


def _resolve_input_shifts(payload: TuningProfileInput) -> dict[str, float]:
    preset = str(payload.preset or "balanced").strip().lower()
    if preset not in SUPPORTED_PRESETS:
        raise ValueError(f"preset must be one of {', '.join(SUPPORTED_PRESETS)}")
    if preset != "custom" and not payload.log_odds_shifts:
        return _preset_shifts(preset)
    return _normalize_log_odds_shifts(payload.log_odds_shifts)


def _preset_shifts(preset: str) -> dict[str, float]:
    base = PRESET_LOG_ODDS_SHIFTS.get(preset, PRESET_LOG_ODDS_SHIFTS["balanced"])
    return {key: float(base.get(key, 0.0)) for key in SUPPORTED_SHIFT_KEYS}


def _match_preset(shifts: dict[str, float]) -> str | None:
    for preset in ("conservative", "balanced", "aggressive"):
        if all(
            abs(shifts.get(key, 0.0) - PRESET_LOG_ODDS_SHIFTS[preset].get(key, 0.0)) < 1e-9
            for key in SUPPORTED_SHIFT_KEYS
        ):
            return preset
    return None


def _normalize_log_odds_shifts(raw: dict[str, float]) -> dict[str, float]:
    normalized = {key: 0.0 for key in SUPPORTED_SHIFT_KEYS}
    for key, value in (raw or {}).items():
        if key not in normalized:
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"log_odds_shifts.{key} must be numeric") from exc
        normalized[key] = max(-_MAX_ABS_SHIFT, min(_MAX_ABS_SHIFT, parsed))
    return normalized


def _smart_money_signal(consensus: float | None, dominant: str | None) -> float:
    if consensus is None:
        return 0.0
    magnitude = max(0.0, min(1.0, abs((consensus - 0.5) * 2.0)))
    if (dominant or "").upper() == "YES":
        return magnitude
    if (dominant or "").upper() == "NO":
        return -magnitude
    return 0.0


def _sibling_signal(implied_prior: float | None) -> float:
    if implied_prior is None:
        return 0.0
    return max(-1.0, min(1.0, (implied_prior - 0.5) * 2.0))


def _neutral_caution_signal(
    model_prob: float,
    score: float | None,
    *,
    threshold: float,
) -> float:
    if score is None:
        return 0.0
    strength = max(0.0, min(1.0, (score - threshold) / max(1.0 - threshold, 1e-9)))
    model_logit = _logit(model_prob)
    if abs(model_logit) < 1e-9:
        return 0.0
    return -math.copysign(strength, model_logit)


def _logit(probability: float) -> float:
    clipped = min(max(probability, 1e-6), 1.0 - 1e-6)
    return math.log(clipped / (1.0 - clipped))


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-value))


def _coerce_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
