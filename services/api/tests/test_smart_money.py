from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from api.smart_money import (
    smart_money_asof,
    smart_money_batch_asof,
    smart_money_view,
)


class _QueryResult:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.result_rows = rows


class _RoutingClickHouse:
    """Routes queries to different canned results based on the ``asof`` parameter.

    ``smart_money_view`` issues two queries: one at ``asked_at`` and one at
    ``asked_at - 24h``. We key on the ``asof`` value to return the right
    snapshot.
    """

    def __init__(self, rows_by_asof: dict[datetime, list[tuple[object, ...]]]) -> None:
        self.rows_by_asof = rows_by_asof
        self.calls: list[tuple[str, dict[str, object] | None]] = []

    async def query(
        self, query: str, parameters: dict[str, object] | None = None
    ) -> _QueryResult:
        self.calls.append((query, parameters))
        asof = (parameters or {}).get("asof")
        assert isinstance(asof, datetime)
        return _QueryResult(list(self.rows_by_asof.get(asof, [])))


def _row(
    condition_id: str,
    *,
    yes_wallets: int = 2,
    no_wallets: int = 1,
    yes_size: float = 150.0,
    no_size: float = 50.0,
    yes_size_usdc: float = 80.0,
    no_size_usdc: float = 20.0,
    yes_avg_entry: float | None = 0.55,
    no_avg_entry: float | None = 0.40,
    net_size_usdc: float = 60.0,
    consensus_score: float = 0.6,
    sample_wallets: int = 3,
    dominant_outcome: str = "YES",
    observed_at: datetime | None = None,
) -> tuple[object, ...]:
    if observed_at is None:
        observed_at = datetime(2026, 4, 23, 12, tzinfo=UTC)
    return (
        condition_id,
        yes_wallets,
        no_wallets,
        yes_size,
        no_size,
        yes_size_usdc,
        no_size_usdc,
        yes_avg_entry,
        no_avg_entry,
        net_size_usdc,
        consensus_score,
        sample_wallets,
        dominant_outcome,
        observed_at,
        observed_at,
    )


@pytest.mark.asyncio
async def test_smart_money_asof_returns_latest_row() -> None:
    asked_at = datetime(2026, 4, 23, 12, tzinfo=UTC)
    ch = _RoutingClickHouse({asked_at: [_row("cond-1")]})

    row = await smart_money_asof(ch, "cond-1", asked_at)

    assert row is not None
    assert row.condition_id == "cond-1"
    assert row.consensus_score == pytest.approx(0.6)
    assert row.dominant_outcome == "YES"


@pytest.mark.asyncio
async def test_smart_money_batch_asof_keys_by_condition_id() -> None:
    asked_at = datetime(2026, 4, 23, 12, tzinfo=UTC)
    ch = _RoutingClickHouse({
        asked_at: [_row("cond-1"), _row("cond-2", consensus_score=-0.2, dominant_outcome="NO")]
    })

    rows = await smart_money_batch_asof(ch, ["cond-1", "cond-2"], asked_at)

    assert set(rows.keys()) == {"cond-1", "cond-2"}
    assert rows["cond-2"].consensus_score == pytest.approx(-0.2)


@pytest.mark.asyncio
async def test_smart_money_view_computes_24h_deltas() -> None:
    asked_at = datetime(2026, 4, 23, 12, tzinfo=UTC)
    prior_asked_at = asked_at - timedelta(hours=24)
    ch = _RoutingClickHouse(
        {
            asked_at: [_row("cond-1", net_size_usdc=200.0, consensus_score=0.8)],
            prior_asked_at: [_row("cond-1", net_size_usdc=50.0, consensus_score=0.3)],
        }
    )

    view = await smart_money_view(ch, "cond-1", asked_at)

    assert view is not None
    assert view.latest.net_size_usdc == pytest.approx(200.0)
    assert view.prior_24h is not None
    assert view.directional_delta_usdc == pytest.approx(150.0)
    assert view.consensus_delta_24h == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_smart_money_view_without_prior_returns_null_deltas() -> None:
    asked_at = datetime(2026, 4, 23, 12, tzinfo=UTC)
    ch = _RoutingClickHouse({asked_at: [_row("cond-1")]})

    view = await smart_money_view(ch, "cond-1", asked_at)

    assert view is not None
    assert view.prior_24h is None
    assert view.directional_delta_usdc is None
    assert view.consensus_delta_24h is None


@pytest.mark.asyncio
async def test_smart_money_asof_returns_none_when_absent() -> None:
    asked_at = datetime(2026, 4, 23, 12, tzinfo=UTC)
    ch = _RoutingClickHouse({asked_at: []})
    assert await smart_money_asof(ch, "nope", asked_at) is None
