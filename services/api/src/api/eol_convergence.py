"""End-of-life convergence ramp (PRD §6.8).

PRD §6.8: "As ``time_to_resolution → 0``, the model's probability must formally
converge to either (a) the realized outcome if observable, or (b) the market
price if not. Mechanism: a monotonically increasing weight on the market price
in the last 5% of a market's life, reaching 1.0 at ``t = 0``. Without this,
last-hour noise creates phantom edges and alerts degrade."

This module is the executable form of that mechanism. The functions are pure
so they can be unit-tested without a database; the markets serve path threads
the inputs through and surfaces the resulting weight on ``MarketModelDetail``
and ``MarketListRow``.
"""

from __future__ import annotations

# Final 5% of a market's life is the ramp window.
RAMP_THRESHOLD_FRACTION = 0.05

# Final hour before resolution: edges/Kelly are suppressed entirely. The
# convergence ramp drives the displayed probability toward market_mid in this
# window so any non-zero edge would be transient last-hour noise.
PHANTOM_EDGE_WINDOW_S = 3600


def final_life_market_mid_weight(
    time_to_resolution_s: float | None,
    total_life_s: float | None,
    *,
    ramp_threshold_fraction: float = RAMP_THRESHOLD_FRACTION,
) -> float | None:
    """Weight to place on ``market_mid`` in the final ``ramp_threshold_fraction`` of life.

    Returns 0.0 when more than ``ramp_threshold_fraction`` of life remains.
    Inside the ramp window the weight grows linearly from 0 at the threshold
    boundary to 1.0 at ``t = 0``. Past resolution (``time_to_resolution_s <= 0``)
    the weight is clamped to 1.0.

    Returns None when either input is None or ``total_life_s`` is non-positive
    (we can't compute a fraction).
    """
    if time_to_resolution_s is None or total_life_s is None:
        return None
    if total_life_s <= 0:
        return None
    if time_to_resolution_s <= 0:
        return 1.0

    threshold_s = total_life_s * ramp_threshold_fraction
    if time_to_resolution_s >= threshold_s:
        return 0.0

    # Linear ramp inside the final fraction: weight(t=threshold)=0, weight(t=0)=1.
    fraction_consumed = 1.0 - (time_to_resolution_s / threshold_s)
    return max(0.0, min(1.0, fraction_consumed))


def apply_eol_convergence(
    model_prob: float | None,
    market_mid: float | None,
    weight: float | None,
) -> float | None:
    """Blend ``model_prob`` toward ``market_mid`` by ``weight``.

    Returns ``model_prob`` unchanged when no ramp applies (weight is None/0)
    or when either input is missing. The returned probability is clipped to
    [0, 1].
    """
    if model_prob is None:
        return None
    if market_mid is None or weight is None or weight <= 0.0:
        return model_prob
    blended = (1.0 - weight) * model_prob + weight * market_mid
    return max(0.0, min(1.0, blended))


def should_suppress_phantom_edge(
    time_to_resolution_s: float | None,
    *,
    threshold_s: float = PHANTOM_EDGE_WINDOW_S,
) -> bool:
    """True iff we're in the final ``threshold_s`` seconds before resolution.

    ``time_to_resolution_s`` is None when ``end_date`` is unknown — in that
    case we don't suppress (the market may be far from resolution).
    Negative values mean the market is past its end_date; we still suppress
    because no fresh edge is meaningful then either.
    """
    if time_to_resolution_s is None:
        return False
    return time_to_resolution_s <= threshold_s
