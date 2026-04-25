"""Privacy audit pipeline (PRD §6 cross-cutting).

Two invariants this module verifies:

1. **No user identifiers in DP aggregates.** ``dp_label_aggregates`` is the
   only public-facing surface that crosses user boundaries; if a row in that
   table contained ``user_id`` (or any equivalent identifier), the
   differential-privacy contract would be broken. We use
   :func:`model.records_contain_only_allowed_fields` to verify each row's
   key set is a subset of the explicitly-allowed columns.

2. **k-anonymity threshold met.** Every persisted aggregate row must have
   ``source_user_count >= min_users`` and ``raw_call_count >= min_calls``.
   The aggregator enforces this on write, but the audit reader proves it
   from the persisted state — the contract is "you can prove from the data
   alone, without knowing the writer code, that no row leaks individual
   identity".

The audit only ever reports counts; no row payloads are exposed in the
result. Surfaced as ``GET /v1/privacy/audit``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from asyncpg import Pool
from model import records_contain_only_allowed_fields

_DP_AGGREGATE_ALLOWED_FIELDS = frozenset(
    {
        "cohort_kind",
        "bucket_label",
        "source_user_count",
        "raw_call_count",
        "noisy_call_count",
        "raw_hit_rate",
        "noisy_hit_rate",
        "epsilon",
        "min_users",
        "min_calls",
        "lookback_start",
        "lookback_end",
        "observed_at",
    }
)


@dataclass(frozen=True)
class DpAggregateAudit:
    rows_inspected: int
    schema_clean: bool
    schema_violations: int
    k_anonymity_violations: int
    weakest_user_count: int | None
    weakest_call_count: int | None


@dataclass(frozen=True)
class PrivacyAuditReport:
    asked_at: datetime
    dp_aggregates: DpAggregateAudit
    invariants_held: bool


def _record_from_row(row: Any) -> dict[str, Any]:
    """asyncpg Records are dict-like; ``dict(row)`` materializes a plain
    mapping the audit can safely walk independent of the ORM layer."""
    return dict(row)


def _audit_dp_aggregate_rows(
    rows: list[dict[str, Any]],
    *,
    allowed_fields: frozenset[str] = _DP_AGGREGATE_ALLOWED_FIELDS,
) -> DpAggregateAudit:
    schema_violations = 0
    k_violations = 0
    weakest_users: int | None = None
    weakest_calls: int | None = None
    for record in rows:
        if not records_contain_only_allowed_fields([record], set(allowed_fields)):
            schema_violations += 1
        # k-anonymity: every published row must clear its declared min_users /
        # min_calls floor. The aggregator enforces this on write — the audit
        # proves it from data alone without trusting the writer.
        users = int(record.get("source_user_count", 0) or 0)
        calls = int(record.get("raw_call_count", 0) or 0)
        min_users = int(record.get("min_users", 0) or 0)
        min_calls = int(record.get("min_calls", 0) or 0)
        if users < min_users or calls < min_calls:
            k_violations += 1
        weakest_users = users if weakest_users is None else min(weakest_users, users)
        weakest_calls = calls if weakest_calls is None else min(weakest_calls, calls)
    return DpAggregateAudit(
        rows_inspected=len(rows),
        schema_clean=schema_violations == 0,
        schema_violations=schema_violations,
        k_anonymity_violations=k_violations,
        weakest_user_count=weakest_users,
        weakest_call_count=weakest_calls,
    )


async def dp_aggregate_audit(pool: Pool) -> DpAggregateAudit:
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM dp_label_aggregates")
    records = [_record_from_row(row) for row in rows]
    return _audit_dp_aggregate_rows(records)


async def run_privacy_audit(*, pool: Pool, asked_at: datetime) -> PrivacyAuditReport:
    dp_audit = await dp_aggregate_audit(pool)
    invariants_held = (
        dp_audit.schema_violations == 0 and dp_audit.k_anonymity_violations == 0
    )
    return PrivacyAuditReport(
        asked_at=asked_at,
        dp_aggregates=dp_audit,
        invariants_held=invariants_held,
    )


def report_to_dict(report: PrivacyAuditReport) -> dict[str, Any]:
    payload = asdict(report)
    if isinstance(payload.get("asked_at"), datetime):
        payload["asked_at"] = report.asked_at.isoformat()
    return payload


__all__ = [
    "DpAggregateAudit",
    "PrivacyAuditReport",
    "dp_aggregate_audit",
    "report_to_dict",
    "run_privacy_audit",
]
