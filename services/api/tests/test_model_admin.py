from __future__ import annotations

from datetime import UTC, datetime

import pytest
from api import drift_monitor, model_admin
from api.model_status import ModelDisableStatus, model_disable_status_asof


class _FakeClickHouse:
    """Append-only in-memory stand-in for the model_disable_log table."""

    def __init__(self) -> None:
        self.rows: list[tuple[object, ...]] = []
        self.columns: tuple[str, ...] = ()
        self.last_query: tuple[str, dict[str, object] | None] | None = None

    async def insert(
        self,
        table: str,
        rows: list[tuple[object, ...]],
        *,
        column_names: tuple[str, ...],
    ) -> None:
        assert table == "model_disable_log"
        self.rows.extend(rows)
        self.columns = column_names

    async def query(self, query: str, parameters: dict[str, object] | None = None) -> _QueryResult:
        self.last_query = (query, parameters)
        market_type = str(parameters["market_type"]) if parameters else ""
        asof = parameters["asof"] if parameters else None
        # Filter to rows for this market_type with observed_at <= asof, latest first.
        market_type_idx = self.columns.index("market_type")
        observed_at_idx = self.columns.index("observed_at")
        filtered = [
            row for row in self.rows
            if str(row[market_type_idx]) == market_type and (asof is None or row[observed_at_idx] <= asof)
        ]
        filtered.sort(key=lambda r: r[observed_at_idx], reverse=True)
        return _QueryResult(filtered[:1])


class _QueryResult:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.result_rows = rows


@pytest.mark.asyncio
async def test_manually_re_enable_writes_re_enabled_row() -> None:
    ch = _FakeClickHouse()
    when = datetime(2026, 4, 25, 10, tzinfo=UTC)
    transition = await model_admin.manually_re_enable_model_type(
        ch,
        market_type="threshold",
        reason="hot-fix landed in PR #1234",
        observed_at=when,
    )
    assert transition.market_type == "threshold"
    assert transition.action == "re_enabled"
    assert transition.observed_at == when
    assert ch.rows
    row = ch.rows[0]
    market_type_idx = ch.columns.index("market_type")
    action_idx = ch.columns.index("action")
    reason_idx = ch.columns.index("reason")
    assert str(row[market_type_idx]) == "threshold"
    assert str(row[action_idx]) == "re_enabled"
    assert str(row[reason_idx]) == "hot-fix landed in PR #1234"


@pytest.mark.asyncio
async def test_manually_disable_writes_disabled_row() -> None:
    ch = _FakeClickHouse()
    when = datetime(2026, 4, 25, 10, tzinfo=UTC)
    transition = await model_admin.manually_disable_model_type(
        ch,
        market_type="threshold",
        observed_at=when,
    )
    assert transition.action == "disabled"
    action_idx = ch.columns.index("action")
    reason_idx = ch.columns.index("reason")
    assert str(ch.rows[0][action_idx]) == "disabled"
    # Default reason kicks in when none provided.
    assert "manual" in str(ch.rows[0][reason_idx]).lower()


@pytest.mark.asyncio
async def test_manual_re_enable_flips_status_asof_immediately() -> None:
    """End-to-end: a manual re-enable row appended via the admin path is
    visible to the asof reader on the next request — no cache, no restart.
    """
    ch = _FakeClickHouse()
    yesterday = datetime(2026, 4, 24, 12, tzinfo=UTC)
    today = datetime(2026, 4, 25, 12, tzinfo=UTC)

    # Pretend a prior nightly drift pass disabled the type yesterday.
    await model_admin.manually_disable_model_type(
        ch,
        market_type="threshold",
        reason="synthetic regression injected yesterday",
        observed_at=yesterday,
    )
    status_before = await model_disable_status_asof(
        ch,
        market_type="threshold",
        asked_at=today,
    )
    assert status_before is not None
    assert status_before.is_disabled is True

    # Operator re-enables manually after retraining.
    await model_admin.manually_re_enable_model_type(
        ch,
        market_type="threshold",
        reason="retrain landed",
        observed_at=today,
    )
    status_after = await model_disable_status_asof(
        ch,
        market_type="threshold",
        asked_at=today,
    )
    assert status_after is not None
    assert status_after.is_disabled is False
    assert status_after.action == "re_enabled"


@pytest.mark.asyncio
async def test_empty_market_type_is_rejected() -> None:
    ch = _FakeClickHouse()
    with pytest.raises(ValueError):
        await model_admin.manually_re_enable_model_type(ch, market_type="   ")


@pytest.mark.asyncio
async def test_synthetic_regression_drift_path_disables_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulate a per-type synthetic regression: 7 consecutive days of
    negative 30d Brier skill produce a ``disabled`` row from the drift
    driver, and the asof reader picks it up so the serve-time gate flips.

    This is the M8.4 stress-test of the drift loop without live data.
    """
    ch = _FakeClickHouse()
    asked_at = datetime(2026, 4, 25, 12, tzinfo=UTC)

    async def _negative_skill_history(
        ch_: object,
        *,
        market_type: str,
        asked_at: datetime,
        lookback_days: int,
    ) -> list[drift_monitor.DailySkillSample]:
        assert market_type == "threshold"
        # 7 consecutive days where the model's brier is materially worse
        # than the baseline — exactly the regression the auto-disable rule
        # is meant to catch.
        return [
            drift_monitor.DailySkillSample(
                asked_at=(asked_at.timestamp() - (6 - idx) * 86_400.0),
                model_brier=0.32,
                baseline_brier=0.20,
            )
            for idx in range(7)
        ]

    async def _no_prior_status(
        ch_: object,
        *,
        market_types: list[str],
        asked_at: datetime,
    ) -> dict[str, ModelDisableStatus]:
        # Type has never been touched yet — exactly the cold-start case
        # where the regression should result in a fresh `disabled` row.
        return {}

    monkeypatch.setattr(drift_monitor, "load_daily_skill_history", _negative_skill_history)
    monkeypatch.setattr(
        drift_monitor, "model_disable_status_batch_asof", _no_prior_status
    )

    rows = await drift_monitor.build_disable_transition_rows(
        ch,
        asked_at=asked_at,
        market_types=["threshold"],
        disable_streak_days=7,
        history_lookback_days=60,
    )
    assert len(rows) == 1
    assert rows[0][0] == "threshold"
    assert rows[0][1] == "disabled"

    # Persist the synthetic disabled row through the same writer path the
    # nightly driver uses — the asof reader must surface it immediately.
    from ingest.writers import MODEL_DISABLE_LOG_COLS

    await ch.insert("model_disable_log", rows, column_names=MODEL_DISABLE_LOG_COLS)
    status = await model_disable_status_asof(
        ch,
        market_type="threshold",
        asked_at=asked_at,
    )
    assert status is not None
    assert status.is_disabled is True
    assert status.action == "disabled"
