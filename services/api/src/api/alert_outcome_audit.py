"""False-positive alert rate audit (M8.5 / PRD §8 product guardrail).

This module turns the M8.5 ``False-positive alert rate < 15%`` exit
criterion into a measurable computation by joining ``signal_events`` with
the ground-truth ``market_resolutions``:

* Each emitted signal carries a ``direction`` (``yes`` / ``no``).
* When the underlying market resolves binary, the alert is correctly
  flagged if the direction matches the ``resolved_outcome``.
* Alerts on markets that haven't resolved (or resolved INVALID) are
  reported as ``skipped`` — they don't contribute to the FP rate
  numerator or denominator. This avoids penalizing signals on long-tail
  markets simply because they haven't had time to resolve yet.

The PRD wording is intentionally short on the FP definition. Using the
final resolution as the truth label is the cleanest interpretation: the
alert was a false positive if a user who acted on it would not have been
on the right side of the eventual outcome.

Like the M1/M2/M3/M4/M7/M8 audits, this module produces evidence but
does not self-certify the exit criterion as passed.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from clickhouse_connect.driver.asyncclient import AsyncClient

from .clickhouse import get_async_client

DEFAULT_LOOKBACK_DAYS: int = 30
DEFAULT_RESOLUTION_GRACE_DAYS: int = 30
DEFAULT_MIN_SAMPLES: int = 20
DEFAULT_FP_RATE_THRESHOLD: float = 0.15
_DIRECTION_LABELS: tuple[str, ...] = ("yes", "no")


@dataclass(frozen=True)
class SignalEventRow:
    event_id: str
    event_type: str
    condition_id: str
    direction: str
    event_time: datetime


@dataclass(frozen=True)
class AlertClassification:
    event_id: str
    event_type: str
    condition_id: str
    direction: str
    event_time: datetime
    resolved_outcome: str | None
    classification: str  # "true_positive", "false_positive", "skipped_unresolved", "skipped_invalid"


@dataclass(frozen=True)
class AlertOutcomeBucket:
    event_type: str
    total_alerts: int
    classified_alerts: int
    skipped_alerts: int
    true_positives: int
    false_positives: int
    fp_rate: float | None
    meets_threshold: bool


@dataclass(frozen=True)
class AlertOutcomeReport:
    asked_at: datetime
    lookback_days: int
    resolution_grace_days: int
    fp_rate_threshold: float
    min_samples: int
    total_alerts: int
    classified_alerts: int
    skipped_alerts: int
    true_positives: int
    false_positives: int
    overall_fp_rate: float | None
    overall_meets_threshold: bool
    by_event_type: list[AlertOutcomeBucket]


def _normalize_direction(raw: object) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip().lower()
    if text in _DIRECTION_LABELS:
        return text
    return None


def _classify_alert(
    *,
    direction: str,
    resolved_outcome: str | None,
) -> str:
    """Bucket a single alert against its ground-truth resolution."""
    if resolved_outcome is None:
        return "skipped_unresolved"
    upper = resolved_outcome.upper()
    if upper not in {"YES", "NO"}:
        # INVALID / unknown / disputed-only-with-no-binary-call
        return "skipped_invalid"
    expected = "yes" if upper == "YES" else "no"
    return "true_positive" if direction == expected else "false_positive"


async def load_signal_events_in_window(
    ch: AsyncClient,
    *,
    asked_at: datetime,
    lookback_days: int,
    resolution_grace_days: int = 0,
    event_types: list[str] | None = None,
) -> list[SignalEventRow]:
    """Pull directional signal events emitted in the trailing window.

    ``resolution_grace_days`` shifts the signal window backwards: signals
    are pulled from ``[asked_at - lookback_days - grace, asked_at - grace]``
    so every alert in the audit has at least ``grace`` days to resolve
    before being matched against its market outcome.
    """
    cutoff = asked_at - timedelta(days=resolution_grace_days)
    conditions: list[str] = [
        "event_time <= {cutoff:DateTime64(3)}",
        "event_time >= {start:DateTime64(3)}",
        "lower(direction) IN ('yes', 'no')",
    ]
    parameters: dict[str, object] = {
        "cutoff": cutoff,
        "start": cutoff - timedelta(days=lookback_days),
    }
    if event_types:
        conditions.append("event_type IN {event_types:Array(String)}")
        parameters["event_types"] = list(event_types)
    where = " AND ".join(conditions)
    result = await ch.query(
        f"""
        SELECT event_id, event_type, condition_id, direction, event_time
          FROM signal_events
         WHERE {where}
         ORDER BY event_time DESC
        """,
        parameters=parameters,
    )
    rows: list[SignalEventRow] = []
    for event_id, event_type, condition_id, direction, event_time in result.result_rows:
        normalized = _normalize_direction(direction)
        if normalized is None:
            continue
        rows.append(
            SignalEventRow(
                event_id=str(event_id),
                event_type=str(event_type),
                condition_id=str(condition_id),
                direction=normalized,
                event_time=event_time,  # type: ignore[arg-type]
            )
        )
    return rows


async def load_first_resolutions(
    ch: AsyncClient,
    *,
    condition_ids: list[str],
    asked_at: datetime,
) -> dict[str, str]:
    """Map condition_id -> first ``resolved_outcome`` (YES / NO / INVALID).

    Uses ``LIMIT 1 BY condition_id ORDER BY observed_at ASC`` so revisions
    do not poison the FP-rate computation (M6.1 PIT contract; matches the
    convention in :mod:`api.kol_credibility`).
    """
    if not condition_ids:
        return {}
    result = await ch.query(
        """
        SELECT condition_id, resolved_outcome
          FROM market_resolutions
         WHERE condition_id IN {conds:Array(String)}
           AND observed_at <= {asof:DateTime64(3)}
         ORDER BY condition_id, observed_at ASC
         LIMIT 1 BY condition_id
        """,
        parameters={
            "conds": list({cid for cid in condition_ids}),
            "asof": asked_at,
        },
    )
    return {str(row[0]): str(row[1] or "").strip().upper() for row in result.result_rows}


def classify_alerts(
    signals: list[SignalEventRow],
    resolutions: dict[str, str],
) -> list[AlertClassification]:
    """Pure classification: each signal → TP / FP / skipped bucket."""
    classifications: list[AlertClassification] = []
    for signal in signals:
        outcome = resolutions.get(signal.condition_id)
        bucket = _classify_alert(
            direction=signal.direction,
            resolved_outcome=outcome,
        )
        classifications.append(
            AlertClassification(
                event_id=signal.event_id,
                event_type=signal.event_type,
                condition_id=signal.condition_id,
                direction=signal.direction,
                event_time=signal.event_time,
                resolved_outcome=outcome if outcome else None,
                classification=bucket,
            )
        )
    return classifications


def _bucket_fp_rate(
    *,
    true_positives: int,
    false_positives: int,
    classified: int,
    min_samples: int,
    fp_threshold: float,
) -> tuple[float | None, bool]:
    if classified == 0:
        return None, False
    fp_rate = false_positives / classified
    meets = classified >= min_samples and fp_rate <= fp_threshold
    return fp_rate, meets


def summarize_alert_outcomes(
    classifications: list[AlertClassification],
    *,
    fp_rate_threshold: float = DEFAULT_FP_RATE_THRESHOLD,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> tuple[
    AlertOutcomeBucket,
    list[AlertOutcomeBucket],
]:
    """Roll up TP/FP counts overall and per event_type."""
    by_type: dict[str, dict[str, int]] = defaultdict(
        lambda: {"total": 0, "tp": 0, "fp": 0, "skipped": 0, "classified": 0}
    )
    overall = {"total": 0, "tp": 0, "fp": 0, "skipped": 0, "classified": 0}
    for cls in classifications:
        bucket = by_type[cls.event_type]
        bucket["total"] += 1
        overall["total"] += 1
        if cls.classification == "true_positive":
            bucket["tp"] += 1
            bucket["classified"] += 1
            overall["tp"] += 1
            overall["classified"] += 1
        elif cls.classification == "false_positive":
            bucket["fp"] += 1
            bucket["classified"] += 1
            overall["fp"] += 1
            overall["classified"] += 1
        else:
            bucket["skipped"] += 1
            overall["skipped"] += 1

    overall_fp_rate, overall_meets = _bucket_fp_rate(
        true_positives=overall["tp"],
        false_positives=overall["fp"],
        classified=overall["classified"],
        min_samples=min_samples,
        fp_threshold=fp_rate_threshold,
    )
    overall_bucket = AlertOutcomeBucket(
        event_type="overall",
        total_alerts=overall["total"],
        classified_alerts=overall["classified"],
        skipped_alerts=overall["skipped"],
        true_positives=overall["tp"],
        false_positives=overall["fp"],
        fp_rate=overall_fp_rate,
        meets_threshold=overall_meets,
    )
    typed_buckets: list[AlertOutcomeBucket] = []
    for event_type, counts in sorted(by_type.items()):
        bucket_fp, bucket_meets = _bucket_fp_rate(
            true_positives=counts["tp"],
            false_positives=counts["fp"],
            classified=counts["classified"],
            min_samples=min_samples,
            fp_threshold=fp_rate_threshold,
        )
        typed_buckets.append(
            AlertOutcomeBucket(
                event_type=event_type,
                total_alerts=counts["total"],
                classified_alerts=counts["classified"],
                skipped_alerts=counts["skipped"],
                true_positives=counts["tp"],
                false_positives=counts["fp"],
                fp_rate=bucket_fp,
                meets_threshold=bucket_meets,
            )
        )
    return overall_bucket, typed_buckets


async def run_alert_outcome_audit(
    ch: AsyncClient,
    *,
    asked_at: datetime | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    resolution_grace_days: int = DEFAULT_RESOLUTION_GRACE_DAYS,
    fp_rate_threshold: float = DEFAULT_FP_RATE_THRESHOLD,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    event_types: list[str] | None = None,
) -> AlertOutcomeReport:
    as_of = asked_at or datetime.now(tz=UTC)
    signals = await load_signal_events_in_window(
        ch,
        asked_at=as_of,
        lookback_days=lookback_days,
        resolution_grace_days=resolution_grace_days,
        event_types=event_types,
    )
    cids = sorted({signal.condition_id for signal in signals})
    # Resolutions are still filtered to observed_at <= asked_at (PIT
    # contract). The grace window only shifts the *signal* window
    # backwards so each alert has time to resolve.
    resolutions = await load_first_resolutions(
        ch, condition_ids=cids, asked_at=as_of
    )
    classifications = classify_alerts(signals, resolutions)
    overall, typed = summarize_alert_outcomes(
        classifications,
        fp_rate_threshold=fp_rate_threshold,
        min_samples=min_samples,
    )
    return AlertOutcomeReport(
        asked_at=as_of,
        lookback_days=lookback_days,
        resolution_grace_days=resolution_grace_days,
        fp_rate_threshold=fp_rate_threshold,
        min_samples=min_samples,
        total_alerts=overall.total_alerts,
        classified_alerts=overall.classified_alerts,
        skipped_alerts=overall.skipped_alerts,
        true_positives=overall.true_positives,
        false_positives=overall.false_positives,
        overall_fp_rate=overall.fp_rate,
        overall_meets_threshold=overall.meets_threshold,
        by_event_type=typed,
    )


def _report_to_json(report: AlertOutcomeReport) -> str:
    payload = asdict(report)
    rate = payload.get("overall_fp_rate")
    if isinstance(rate, float):
        payload["overall_fp_rate"] = round(rate, 6)
    for bucket in payload.get("by_event_type", []):
        value = bucket.get("fp_rate")
        if isinstance(value, float):
            bucket["fp_rate"] = round(value, 6)
    return json.dumps(payload, indent=2, default=str)


async def _main() -> None:
    ch = await get_async_client()
    try:
        report = await run_alert_outcome_audit(ch)
        print(_report_to_json(report))
    finally:
        await ch.close()


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
