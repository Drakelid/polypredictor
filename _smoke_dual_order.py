"""Verify the rewritten queries are syntactically accepted by ClickHouse 24.8."""
import asyncio
from datetime import UTC, datetime

from api.clickhouse import get_async_client
from api.markets import _latest_markets


async def go() -> None:
    ch = await get_async_client()
    try:
        # Apply migrations first.
        from api.migrations import apply_clickhouse_migrations
        applied = await apply_clickhouse_migrations(
            ch, "services/ingest/migrations/clickhouse"
        )
        print("migrations applied:", len(applied))

        # Hit the previously-broken markets list query.
        rows = await _latest_markets(ch, datetime.now(UTC), limit=10)
        print("_latest_markets rows:", len(rows))

        # Hit several other rewritten queries to confirm they parse.
        from api.event_time import scheduled_events_asof
        events = await scheduled_events_asof(ch, "fake-cond", datetime.now(UTC))
        print("scheduled_events_asof rows:", len(events))

        from api.backtest_smoke import _resolved_yes_no_markets
        resolved = await _resolved_yes_no_markets(ch, limit=5)
        print("backtest_smoke resolved rows:", len(resolved))

        # Direct dual-ORDER syntax check: the rewritten markets query above
        # is the canonical case that triggered the production crash.
        print("PASS: all rewritten queries accepted by ClickHouse 24.8")
    finally:
        await ch.close()


asyncio.run(go())
