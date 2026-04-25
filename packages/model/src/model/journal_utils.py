"""
Helpers for journal call scoring and PnL calculation.

In Polypredictor, users log their own calls (YES/NO bets) on markets. Once
the market resolves, those calls are scored for both calibration (Brier) and
profit/loss. This module contains lightweight functions for representing a
journal entry and computing its PnL and Brier contribution. These helpers
serve as stand‑ins for a full journal subsystem.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class JournalCall:
    """Simple representation of a user’s call on a binary market.

    Attributes
    ----------
    condition_id:
        Identifier of the market.
    call_time:
        When the call was made.
    predicted_prob:
        Probability assigned to YES at the time of the call (0 ≤ p ≤ 1).
    stake:
        Amount wagered in arbitrary units (e.g. dollars). Positive stake
        implies buying YES; negative stake would represent a short position.
    resolved_outcome:
        Actual outcome of the market, 1 for YES and 0 for NO. None if
        unresolved.
    resolve_time:
        Timestamp of market resolution, if known.
    """
    condition_id: str
    call_time: datetime
    predicted_prob: float
    stake: float
    resolved_outcome: Optional[int] = None
    resolve_time: Optional[datetime] = None


def compute_pnl(call: JournalCall) -> float:
    """Compute the PnL for a resolved call.

    PnL is calculated as ``stake * (outcome - predicted_prob)``. When the
    outcome is 1, a long YES position earns ``stake * (1 - predicted_prob)``.
    When the outcome is 0, the position loses ``stake * predicted_prob``.
    Unresolved calls return NaN.

    Returns
    -------
    float
        Profit/loss for the call, or NaN if unresolved.
    """
    if call.resolved_outcome is None:
        return float("nan")
    outcome = 1.0 if call.resolved_outcome else 0.0
    return call.stake * (outcome - call.predicted_prob)


def compute_call_brier(call: JournalCall) -> float:
    """Compute the Brier score for a resolved journal call.

    Brier score is ``(predicted_prob - outcome)**2``. Unresolved calls return NaN.
    """
    if call.resolved_outcome is None:
        return float("nan")
    outcome = 1.0 if call.resolved_outcome else 0.0
    return (call.predicted_prob - outcome) ** 2