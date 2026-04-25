"""Whale-flow detector (M3.3, PRD §3.3).

Pure diff against two snapshots of ``positions_smart_money``:

* current — the positions just written by the current
  ``smart_money_refresh`` pass (already in memory; we don't re-read them).
* prior  — the latest rows per (wallet, condition_id, outcome) strictly
  before the current pass, restricted to the qualified cohort.

For each (wallet, condition_id, outcome) we emit at most one event per
refresh, typed ``whale_open`` / ``whale_resize`` / ``whale_close``. A change
qualifies if either the absolute USDC notional delta clears the open
threshold (default 10k USDC), OR the delta is at least ``wallet_size_pct``
of the larger of the prior/current USDC size (default 20%).

Severity is ``|delta_usdc| / open_threshold`` so downstream consumers can
sort a mixed feed without re-opening the payload.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from polymarket_client import Position

WHALE_OPEN_THRESHOLD_USDC_DEFAULT = 10_000.0
WHALE_RESIZE_PCT_DEFAULT = 0.20
WHALE_MIN_REFERENCE_USDC_DEFAULT = 1_000.0


@dataclass(frozen=True)
class WhaleFlowThresholds:
    open_threshold_usdc: float = WHALE_OPEN_THRESHOLD_USDC_DEFAULT
    resize_pct: float = WHALE_RESIZE_PCT_DEFAULT
    # The resize_pct rule alone trivially fires for any new open (100% of
    # itself is > 20%). Require the reference size to clear this floor before
    # the fractional-move branch qualifies — opens below this floor must
    # instead breach the absolute USDC threshold.
    min_reference_usdc: float = WHALE_MIN_REFERENCE_USDC_DEFAULT


@dataclass(frozen=True)
class PositionSnapshot:
    """Minimal position view that both current + prior snapshots share.

    ``size_usdc`` is the USDC notional the wallet is on the line for; prior
    snapshots come from ClickHouse, current snapshots come from the live
    Polymarket ``/positions`` response, and both normalize to this shape.
    """

    proxy_wallet: str
    condition_id: str
    token_id: str
    outcome: str  # 'YES' | 'NO'
    size: float
    size_usdc: float
    avg_entry_price: float


@dataclass(frozen=True)
class WhaleFlowEvent:
    event_id: str
    event_type: str   # 'whale_open' | 'whale_resize' | 'whale_close'
    proxy_wallet: str
    condition_id: str
    outcome: str
    direction: str    # 'yes' | 'no'
    token_id: str
    size_delta_usdc: float
    prior_size_usdc: float
    current_size_usdc: float
    severity: float
    event_time: datetime


def positions_from_polymarket(positions: list[Position]) -> list[PositionSnapshot]:
    return [
        PositionSnapshot(
            proxy_wallet=position.proxy_wallet,
            condition_id=position.condition_id,
            token_id=position.token_id,
            outcome=position.outcome.upper(),
            size=float(position.size),
            size_usdc=float(position.current_value),
            avg_entry_price=float(position.avg_price),
        )
        for position in positions
        if float(position.size) > 0.0
    ]


def _key(snapshot: PositionSnapshot) -> tuple[str, str, str]:
    return (snapshot.proxy_wallet, snapshot.condition_id, snapshot.outcome)


def _event_id(
    *,
    wallet: str,
    condition_id: str,
    outcome: str,
    observed_at: datetime,
) -> str:
    seed = f"{wallet}|{condition_id}|{outcome}|{observed_at.isoformat()}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


def _qualifies(
    *,
    delta: float,
    reference_usdc: float,
    thresholds: WhaleFlowThresholds,
) -> bool:
    if abs(delta) >= thresholds.open_threshold_usdc:
        return True
    return (
        reference_usdc >= thresholds.min_reference_usdc
        and abs(delta) >= thresholds.resize_pct * reference_usdc
    )


def detect_whale_flow(
    *,
    current: list[PositionSnapshot],
    prior: list[PositionSnapshot],
    qualified_wallets: set[str],
    observed_at: datetime,
    thresholds: WhaleFlowThresholds | None = None,
) -> list[WhaleFlowEvent]:
    """Emit whale-flow events for qualified-wallet position changes.

    Only wallets in ``qualified_wallets`` are considered — unqualified
    leaderboard hits do not produce signals even if they hit thresholds,
    consistent with the PRD's wash-trading guards on top of the signal.
    """

    thresholds = thresholds or WhaleFlowThresholds()

    current_map = {
        _key(snapshot): snapshot
        for snapshot in current
        if snapshot.proxy_wallet in qualified_wallets
    }
    prior_map = {
        _key(snapshot): snapshot
        for snapshot in prior
        if snapshot.proxy_wallet in qualified_wallets
    }

    events: list[WhaleFlowEvent] = []
    touched: set[tuple[str, str, str]] = set()

    # Opens + resizes + closes keyed on positions that exist now OR existed
    # previously; diffing a union prevents missed closes.
    for key in set(current_map.keys()) | set(prior_map.keys()):
        if key in touched:
            continue
        touched.add(key)
        curr = current_map.get(key)
        prev = prior_map.get(key)
        current_usdc = curr.size_usdc if curr is not None else 0.0
        prior_usdc = prev.size_usdc if prev is not None else 0.0
        delta = current_usdc - prior_usdc
        reference_usdc = max(current_usdc, prior_usdc)
        if not _qualifies(delta=delta, reference_usdc=reference_usdc, thresholds=thresholds):
            continue

        if prev is None:
            event_type = "whale_open"
        elif curr is None or curr.size_usdc <= 0.0:
            event_type = "whale_close"
        else:
            event_type = "whale_resize"

        wallet = (curr or prev).proxy_wallet  # type: ignore[union-attr]
        condition_id = (curr or prev).condition_id  # type: ignore[union-attr]
        outcome = (curr or prev).outcome  # type: ignore[union-attr]
        token_id = (curr or prev).token_id  # type: ignore[union-attr]
        direction = outcome.lower() if outcome in {"YES", "NO"} else "neutral"
        severity = (
            abs(delta) / thresholds.open_threshold_usdc
            if thresholds.open_threshold_usdc > 0
            else abs(delta)
        )
        events.append(
            WhaleFlowEvent(
                event_id=_event_id(
                    wallet=wallet,
                    condition_id=condition_id,
                    outcome=outcome,
                    observed_at=observed_at,
                ),
                event_type=event_type,
                proxy_wallet=wallet,
                condition_id=condition_id,
                outcome=outcome,
                direction=direction,
                token_id=token_id,
                size_delta_usdc=delta,
                prior_size_usdc=prior_usdc,
                current_size_usdc=current_usdc,
                severity=severity,
                event_time=observed_at,
            )
        )

    events.sort(key=lambda event: event.severity, reverse=True)
    return events
