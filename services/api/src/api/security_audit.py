"""Security key-rotation audit (PRD §9 cross-cutting).

Operators are required to rotate stored CLOB / API credentials on a regular
cadence (default 90 days). The :func:`model.needs_rotation` helper encodes
the date arithmetic; this module queries ``user_api_keys_encrypted`` and
aggregates per provider so the dashboard can flag stale credentials WITHOUT
surfacing user_id-keyed data (privacy first — see ``records_contain_only_allowed_fields``).

Surface rules:
* Only aggregate counts and provider names are returned; no user identifiers.
* ``last_rotated_at`` defaults to ``created_at`` when ``rotated_at`` is null.
* Threshold is configurable via the ``rotation_interval_days`` parameter so
  a CISO can spot-check at e.g. 30 days without changing the default.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime

from asyncpg import Pool
from model import needs_rotation


@dataclass(frozen=True)
class RotationProviderSummary:
    provider: str
    total_credentials: int
    needs_rotation_count: int
    oldest_rotation_age_days: float | None


@dataclass(frozen=True)
class RotationAuditReport:
    asked_at: datetime
    rotation_interval_days: int
    total_credentials: int
    needs_rotation_count: int
    providers: list[RotationProviderSummary]


def _coerce_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


async def rotation_audit(
    *,
    pool: Pool,
    asked_at: datetime,
    rotation_interval_days: int = 90,
) -> RotationAuditReport:
    """Aggregate credential rotation state per provider.

    Uses ``needs_rotation`` against either ``rotated_at`` (preferred) or
    ``created_at`` (when never rotated). Returns purely aggregate counts —
    no user identifiers leave the function.
    """
    if rotation_interval_days <= 0:
        raise ValueError("rotation_interval_days must be positive")

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT provider, created_at, rotated_at
            FROM user_api_keys_encrypted
            """
        )

    totals: Counter[str] = Counter()
    needs_count: Counter[str] = Counter()
    oldest_age_seconds: dict[str, float] = {}

    for row in rows:
        provider = str(row["provider"])
        rotated_at = _coerce_utc(row["rotated_at"]) or _coerce_utc(row["created_at"])
        totals[provider] += 1
        if rotated_at is None:
            # Never recorded a creation timestamp — treat as needs rotation.
            needs_count[provider] += 1
            continue
        if needs_rotation(rotated_at, rotation_interval_days):
            needs_count[provider] += 1
        age_seconds = max((asked_at - rotated_at).total_seconds(), 0.0)
        prior = oldest_age_seconds.get(provider, 0.0)
        if age_seconds > prior:
            oldest_age_seconds[provider] = age_seconds

    providers = [
        RotationProviderSummary(
            provider=provider,
            total_credentials=totals[provider],
            needs_rotation_count=needs_count[provider],
            oldest_rotation_age_days=(
                oldest_age_seconds[provider] / 86_400.0
                if provider in oldest_age_seconds
                else None
            ),
        )
        for provider in sorted(totals)
    ]
    return RotationAuditReport(
        asked_at=asked_at,
        rotation_interval_days=rotation_interval_days,
        total_credentials=sum(totals.values()),
        needs_rotation_count=sum(needs_count.values()),
        providers=providers,
    )


__all__ = [
    "RotationAuditReport",
    "RotationProviderSummary",
    "rotation_audit",
]
