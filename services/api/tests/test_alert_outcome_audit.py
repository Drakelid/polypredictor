from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from api import alert_outcome_audit
from api.alert_outcome_audit import (
    DEFAULT_FP_RATE_THRESHOLD,
    DEFAULT_MIN_SAMPLES,
    AlertClassification,
    SignalEventRow,
    classify_alerts,
    load_first_resolutions,
    load_signal_events_in_window,
    summarize_alert_outcomes,
)


class _QueryResult:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.result_rows = rows


class _FakeClickHouse:
    def __init__(self, responses: list[list[tuple[object, ...]]]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, dict[str, object] | None]] = []

    async def query(
        self, query: str, parameters: dict[str, object] | None = None
    ) -> _QueryResult:
        self.calls.append((query, parameters))
        return _QueryResult(self._responses[len(self.calls) - 1])


_NOW = datetime(2026, 4, 26, 12, tzinfo=UTC)


def _signal(
    *,
    event_id: str = "evt",
    event_type: str = "whale",
    condition_id: str = "cond-1",
    direction: str = "yes",
    days_ago: float = 1.0,
) -> SignalEventRow:
    return SignalEventRow(
        event_id=event_id,
        event_type=event_type,
        condition_id=condition_id,
        direction=direction,
        event_time=_NOW - timedelta(days=days_ago),
    )


# ---------------------------------------------------------------------------
# classify_alerts
# ---------------------------------------------------------------------------


def test_classify_alerts_marks_yes_alert_against_yes_outcome_as_true_positive() -> None:
    signals = [_signal(direction="yes", condition_id="c1")]
    resolutions = {"c1": "YES"}

    classifications = classify_alerts(signals, resolutions)

    assert len(classifications) == 1
    assert classifications[0].classification == "true_positive"


def test_classify_alerts_marks_yes_alert_against_no_outcome_as_false_positive() -> None:
    signals = [_signal(direction="yes", condition_id="c1")]
    resolutions = {"c1": "NO"}

    classifications = classify_alerts(signals, resolutions)

    assert classifications[0].classification == "false_positive"


def test_classify_alerts_marks_no_alert_against_no_outcome_as_true_positive() -> None:
    signals = [_signal(direction="no", condition_id="c1")]
    resolutions = {"c1": "NO"}

    assert classify_alerts(signals, resolutions)[0].classification == "true_positive"


def test_classify_alerts_skips_unresolved_market() -> None:
    signals = [_signal(condition_id="c1")]
    resolutions: dict[str, str] = {}

    assert classify_alerts(signals, resolutions)[0].classification == "skipped_unresolved"


def test_classify_alerts_skips_invalid_resolution() -> None:
    signals = [_signal(condition_id="c1")]
    resolutions = {"c1": "INVALID"}

    assert classify_alerts(signals, resolutions)[0].classification == "skipped_invalid"


def test_classify_alerts_handles_unknown_resolved_outcome_string() -> None:
    signals = [_signal(condition_id="c1")]
    resolutions = {"c1": "DISPUTED"}

    assert classify_alerts(signals, resolutions)[0].classification == "skipped_invalid"


# ---------------------------------------------------------------------------
# summarize_alert_outcomes
# ---------------------------------------------------------------------------


def _classification(
    *,
    event_type: str = "whale",
    classification: str = "true_positive",
    direction: str = "yes",
    condition_id: str = "cond-1",
) -> AlertClassification:
    return AlertClassification(
        event_id=f"{event_type}-{condition_id}-{classification}",
        event_type=event_type,
        condition_id=condition_id,
        direction=direction,
        event_time=_NOW,
        resolved_outcome="YES" if classification == "true_positive" else "NO",
        classification=classification,
    )


def test_summarize_alert_outcomes_overall_fp_rate_excludes_skipped() -> None:
    classifications = [
        _classification(classification="true_positive"),
        _classification(classification="false_positive"),
        _classification(classification="false_positive"),
        _classification(classification="skipped_unresolved"),
        _classification(classification="skipped_invalid"),
    ]

    overall, _ = summarize_alert_outcomes(
        classifications,
        fp_rate_threshold=DEFAULT_FP_RATE_THRESHOLD,
        min_samples=1,
    )

    assert overall.total_alerts == 5
    assert overall.classified_alerts == 3
    assert overall.skipped_alerts == 2
    assert overall.true_positives == 1
    assert overall.false_positives == 2
    assert overall.fp_rate == pytest.approx(2 / 3)


def test_summarize_alert_outcomes_meets_threshold_only_above_min_samples() -> None:
    classifications = [
        _classification(classification="true_positive"),
        _classification(classification="false_positive"),
    ]

    overall, _ = summarize_alert_outcomes(
        classifications,
        fp_rate_threshold=0.6,
        min_samples=10,
    )

    assert overall.fp_rate == pytest.approx(0.5)
    assert overall.meets_threshold is False  # below min_samples


def test_summarize_alert_outcomes_meets_threshold_when_within_band() -> None:
    classifications = [
        _classification(classification="true_positive") for _ in range(9)
    ] + [_classification(classification="false_positive")]

    overall, _ = summarize_alert_outcomes(
        classifications,
        fp_rate_threshold=DEFAULT_FP_RATE_THRESHOLD,
        min_samples=DEFAULT_MIN_SAMPLES,
    )

    # 1/10 = 10% < 15% threshold; classified=10 still below default
    # min_samples=20, so meets_threshold stays False until enough data
    # has accumulated.
    assert overall.fp_rate == pytest.approx(0.1)
    assert overall.meets_threshold is False


def test_summarize_alert_outcomes_buckets_by_event_type() -> None:
    classifications = [
        _classification(event_type="whale", classification="true_positive"),
        _classification(event_type="whale", classification="false_positive"),
        _classification(event_type="arb", classification="true_positive"),
        _classification(event_type="arb", classification="true_positive"),
        _classification(event_type="arb", classification="false_positive"),
    ]

    _, by_type = summarize_alert_outcomes(
        classifications,
        fp_rate_threshold=DEFAULT_FP_RATE_THRESHOLD,
        min_samples=1,
    )

    by_event = {bucket.event_type: bucket for bucket in by_type}
    assert by_event["whale"].fp_rate == pytest.approx(0.5)
    assert by_event["arb"].fp_rate == pytest.approx(1 / 3)


def test_summarize_alert_outcomes_handles_all_skipped() -> None:
    classifications = [
        _classification(classification="skipped_unresolved"),
        _classification(classification="skipped_invalid"),
    ]

    overall, _ = summarize_alert_outcomes(
        classifications,
        fp_rate_threshold=DEFAULT_FP_RATE_THRESHOLD,
        min_samples=1,
    )

    assert overall.classified_alerts == 0
    assert overall.fp_rate is None
    assert overall.meets_threshold is False


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_signal_events_in_window_normalizes_direction_case() -> None:
    ch = _FakeClickHouse(
        [
            [
                ("evt-1", "whale", "c1", "YES", _NOW - timedelta(days=1)),
                ("evt-2", "arb", "c2", "no", _NOW - timedelta(days=2)),
                ("evt-3", "whale", "c3", "neutral", _NOW - timedelta(days=3)),
            ]
        ]
    )

    events = await load_signal_events_in_window(
        ch,  # type: ignore[arg-type]
        asked_at=_NOW,
        lookback_days=30,
    )

    # The 'neutral' direction is filtered out client-side as a defense in
    # depth; SQL also filters but mocked CH returns all rows.
    assert {e.event_id for e in events} == {"evt-1", "evt-2"}
    assert {e.direction for e in events} == {"yes", "no"}


@pytest.mark.asyncio
async def test_load_signal_events_in_window_grace_shifts_window_backwards() -> None:
    ch = _FakeClickHouse([[]])

    await load_signal_events_in_window(
        ch,  # type: ignore[arg-type]
        asked_at=_NOW,
        lookback_days=10,
        resolution_grace_days=5,
    )

    _, params = ch.calls[0]
    assert params is not None
    assert params["cutoff"] == _NOW - timedelta(days=5)
    assert params["start"] == _NOW - timedelta(days=15)


@pytest.mark.asyncio
async def test_load_first_resolutions_normalizes_outcome_case() -> None:
    ch = _FakeClickHouse([[("c1", "YES"), ("c2", "no"), ("c3", "Invalid")]])

    out = await load_first_resolutions(
        ch,  # type: ignore[arg-type]
        condition_ids=["c1", "c2", "c3"],
        asked_at=_NOW,
    )

    assert out == {"c1": "YES", "c2": "NO", "c3": "INVALID"}


@pytest.mark.asyncio
async def test_load_first_resolutions_short_circuits_on_empty_input() -> None:
    ch = _FakeClickHouse([])

    out = await load_first_resolutions(
        ch,  # type: ignore[arg-type]
        condition_ids=[],
        asked_at=_NOW,
    )

    assert out == {}
    assert ch.calls == []


# ---------------------------------------------------------------------------
# Top-level driver
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_alert_outcome_audit_aggregates_pieces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def _fake_signals(
        ch, *, asked_at, lookback_days, resolution_grace_days, event_types=None
    ):
        captured["lookback_days"] = lookback_days
        captured["grace"] = resolution_grace_days
        return [
            _signal(event_id="e-tp", event_type="whale", direction="yes", condition_id="c1"),
            _signal(event_id="e-fp", event_type="whale", direction="no", condition_id="c1"),
            _signal(event_id="e-skip", event_type="arb", direction="yes", condition_id="c2"),
        ]

    async def _fake_resolutions(ch, *, condition_ids, asked_at):
        captured["cids"] = sorted(condition_ids)
        return {"c1": "YES"}

    monkeypatch.setattr(alert_outcome_audit, "load_signal_events_in_window", _fake_signals)
    monkeypatch.setattr(alert_outcome_audit, "load_first_resolutions", _fake_resolutions)

    report = await alert_outcome_audit.run_alert_outcome_audit(
        object(),  # type: ignore[arg-type]
        asked_at=_NOW,
        lookback_days=14,
        resolution_grace_days=3,
        fp_rate_threshold=0.25,
        min_samples=1,
    )

    assert captured["lookback_days"] == 14
    assert captured["grace"] == 3
    assert captured["cids"] == ["c1", "c2"]
    assert report.total_alerts == 3
    assert report.classified_alerts == 2
    assert report.skipped_alerts == 1
    assert report.true_positives == 1
    assert report.false_positives == 1
    assert report.overall_fp_rate == pytest.approx(0.5)
    assert report.overall_meets_threshold is False
    by_event = {bucket.event_type: bucket for bucket in report.by_event_type}
    assert "whale" in by_event
    assert "arb" in by_event
    assert by_event["arb"].classified_alerts == 0
