"""Admin operations on the per-type ensemble auto-disable state (M8.4).

The drift driver auto-disables a per-type model when the 30d Brier-skill
streak goes negative for ``settings.drift_monitor_disable_streak_days``.
After a fix-and-retrain operators need to bring the model back online
without waiting for the next nightly drift pass to flip the state on its
own. This module is the manual override:

* :func:`manually_re_enable_model_type` — appends a ``re_enabled`` row to
  ``model_disable_log`` (the same append-only table the drift driver
  writes, so the asof reader treats the manual row identically).
* :func:`manually_disable_model_type` — symmetric admin disable, useful
  when an oncall wants to pre-emptively bypass the ensemble before the
  nightly pass picks it up.

PIT correctness is preserved: the entry uses ``observed_at = now``, and
:func:`api.model_status.model_disable_status_asof` already resolves the
latest action ordered by ``observed_at`` so a manual row immediately
flips the serve-time gate without restarting anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from clickhouse_connect.driver.asyncclient import AsyncClient
from ingest.writers import MODEL_DISABLE_LOG_COLS, model_disable_log_row


@dataclass(frozen=True)
class ManualTransition:
    market_type: str
    action: str
    reason: str
    observed_at: datetime


_VALID_ACTIONS = frozenset({"disabled", "re_enabled"})


def _normalize_market_type(raw: str) -> str:
    cleaned = (raw or "").strip()
    if not cleaned:
        raise ValueError("market_type is required")
    return cleaned


def _normalize_reason(raw: str | None, *, default: str) -> str:
    cleaned = (raw or "").strip()
    return cleaned or default


async def _append_transition(
    ch: AsyncClient,
    *,
    market_type: str,
    action: str,
    reason: str,
    threshold_days: int,
    observed_at: datetime | None,
) -> ManualTransition:
    if action not in _VALID_ACTIONS:
        raise ValueError(f"action must be one of {sorted(_VALID_ACTIONS)}, got {action!r}")
    market_type = _normalize_market_type(market_type)
    when = observed_at or datetime.now(tz=UTC)
    row = model_disable_log_row(
        market_type=market_type,
        action=action,
        consecutive_days=0,
        threshold_days=int(threshold_days),
        reason=reason,
        observed_at=when,
    )
    await ch.insert(
        "model_disable_log",
        [row],
        column_names=MODEL_DISABLE_LOG_COLS,
    )
    return ManualTransition(
        market_type=market_type,
        action=action,
        reason=reason,
        observed_at=when,
    )


async def manually_re_enable_model_type(
    ch: AsyncClient,
    *,
    market_type: str,
    reason: str | None = None,
    threshold_days: int = 7,
    observed_at: datetime | None = None,
) -> ManualTransition:
    """Append a manual ``re_enabled`` row for ``market_type``.

    The row is timestamped at ``observed_at`` (default: ``now``) so the
    asof reader picks it up on the next request — no restart needed.
    """
    return await _append_transition(
        ch,
        market_type=market_type,
        action="re_enabled",
        reason=_normalize_reason(reason, default="manual re-enable"),
        threshold_days=threshold_days,
        observed_at=observed_at,
    )


async def manually_disable_model_type(
    ch: AsyncClient,
    *,
    market_type: str,
    reason: str | None = None,
    threshold_days: int = 7,
    observed_at: datetime | None = None,
) -> ManualTransition:
    """Append a manual ``disabled`` row for ``market_type``.

    Useful when an oncall operator wants to pre-emptively bypass the
    ensemble without waiting for the nightly drift pass to flip the state.
    """
    return await _append_transition(
        ch,
        market_type=market_type,
        action="disabled",
        reason=_normalize_reason(reason, default="manual disable"),
        threshold_days=threshold_days,
        observed_at=observed_at,
    )


__all__ = [
    "ManualTransition",
    "manually_disable_model_type",
    "manually_re_enable_model_type",
]
