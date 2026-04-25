"""Sibling / no-arb checker for M3.4.

This worker reads the latest active market snapshots, classifications, and
YES-leg quotes from ClickHouse, then:

* builds sibling constraints across threshold markets
* groups mutually-exclusive multi-outcome legs by event
* emits ``arb`` signal events for violations that remain profitable after the
  configured taker-fee allowance
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import structlog
from model import (
    MarketType,
    SiblingMarket,
    SiblingQuote,
    build_multi_outcome_groups,
    build_threshold_pairs,
    detect_multi_outcome_arbs,
    detect_threshold_arbs,
)

from ..clickhouse import get_async_client
from ..dlq import DeadLetterQueue
from ..settings import get_settings
from ..writers import SIGNAL_EVENTS_COLS, signal_event_row, utcnow

log = structlog.get_logger(__name__)


async def _latest_active_markets(
    ch: object,
    *,
    asked_at: datetime,
    limit: int,
) -> list[dict[str, object]]:
    query = """
        SELECT condition_id, event_id, question, token_ids, active, closed, archived
        FROM markets_snapshots
        WHERE observed_at <= {asof:DateTime64(3)}
        ORDER BY condition_id, observed_at DESC
        LIMIT 1 BY condition_id
    """
    result = await ch.query(query, parameters={"asof": asked_at})  # type: ignore[attr-defined]
    out: list[dict[str, object]] = []
    for row in result.result_rows[:limit]:
        if not bool(row[4]) or bool(row[5]) or bool(row[6]):
            continue
        condition_id = str(row[0])
        event_id = str(row[1] or "")
        question = str(row[2])
        token_ids = list(row[3]) if row[3] else []
        out.append(
            {
                "condition_id": condition_id,
                "event_id": event_id,
                "question": question,
                "token_ids": token_ids,
            }
        )
    return out


async def _latest_classifications(
    ch: object,
    condition_ids: list[str],
    *,
    asked_at: datetime,
) -> dict[str, dict[str, object]]:
    if not condition_ids:
        return {}
    query = """
        SELECT condition_id, market_type, asset, strike, direction, resolution_date
        FROM market_classifications
        WHERE condition_id IN {conds:Array(String)}
          AND observed_at <= {asof:DateTime64(3)}
        ORDER BY condition_id, observed_at DESC
        LIMIT 1 BY condition_id
    """
    result = await ch.query(  # type: ignore[attr-defined]
        query,
        parameters={"conds": condition_ids, "asof": asked_at},
    )
    return {
        str(row[0]): {
            "market_type": MarketType(str(row[1])),
            "asset": str(row[2]) if row[2] else None,
            "strike": float(row[3]) if row[3] is not None else None,
            "direction": str(row[4]) if row[4] else None,
            "resolution_date": row[5],
        }
        for row in result.result_rows
    }


async def _latest_yes_quotes(
    ch: object,
    token_ids: list[str],
    *,
    asked_at: datetime,
) -> dict[str, SiblingQuote]:
    if not token_ids:
        return {}
    query = """
        SELECT token_id, mid, best_bid, best_ask, bid_size, ask_size
        FROM market_quotes
        WHERE token_id IN {toks:Array(String)}
          AND observed_at <= {asof:DateTime64(3)}
        ORDER BY token_id, observed_at DESC
        LIMIT 1 BY token_id
    """
    result = await ch.query(  # type: ignore[attr-defined]
        query,
        parameters={"toks": token_ids, "asof": asked_at},
    )
    return {
        str(row[0]): SiblingQuote(
            mid=float(row[1]) if row[1] is not None else None,
            best_bid=float(row[2]) if row[2] is not None else None,
            best_ask=float(row[3]) if row[3] is not None else None,
            bid_size=float(row[4]) if row[4] is not None else None,
            ask_size=float(row[5]) if row[5] is not None else None,
        )
        for row in result.result_rows
    }


def _build_sibling_markets(
    snapshots: list[dict[str, object]],
    classifications: dict[str, dict[str, object]],
    quotes: dict[str, SiblingQuote],
) -> list[SiblingMarket]:
    out: list[SiblingMarket] = []
    for snapshot in snapshots:
        condition_id = str(snapshot["condition_id"])
        classification = classifications.get(condition_id)
        if classification is None:
            continue
        market_type = classification["market_type"]
        if market_type not in {MarketType.THRESHOLD, MarketType.MULTI_OUTCOME}:
            continue
        token_ids = list(snapshot.get("token_ids") or [])
        yes_token = str(token_ids[0]) if token_ids else ""
        out.append(
            SiblingMarket(
                condition_id=condition_id,
                question=str(snapshot["question"]),
                event_id=str(snapshot.get("event_id") or "") or None,
                market_type=market_type,  # type: ignore[arg-type]
                asset=classification.get("asset"),  # type: ignore[arg-type]
                direction=classification.get("direction"),  # type: ignore[arg-type]
                strike=classification.get("strike"),  # type: ignore[arg-type]
                resolution_date=classification.get("resolution_date"),  # type: ignore[arg-type]
                quote=quotes.get(yes_token, SiblingQuote(None, None, None)),
            )
        )
    return out


async def run_once() -> int:
    settings = get_settings()
    observed_at = utcnow()
    ch = await get_async_client()
    try:
        snapshots = await _latest_active_markets(
            ch,
            asked_at=observed_at,
            limit=settings.arb_checker_max_markets,
        )
        condition_ids = [str(snapshot["condition_id"]) for snapshot in snapshots]
        classifications = await _latest_classifications(
            ch,
            condition_ids,
            asked_at=observed_at,
        )
        yes_tokens = [
            str(token_ids[0])
            for snapshot in snapshots
            if (token_ids := list(snapshot.get("token_ids") or []))
        ]
        quotes = await _latest_yes_quotes(ch, yes_tokens, asked_at=observed_at)
        sibling_markets = _build_sibling_markets(snapshots, classifications, quotes)
        violations = [
            *detect_threshold_arbs(
                build_threshold_pairs(sibling_markets),
                event_time=observed_at,
                taker_fee_bps=settings.arb_taker_fee_bps,
            ),
            *detect_multi_outcome_arbs(
                build_multi_outcome_groups(sibling_markets),
                event_time=observed_at,
                taker_fee_bps=settings.arb_taker_fee_bps,
            ),
        ]
        if not violations:
            log.info("arb_checker.done", events=0)
            return 0
        rows = [
            signal_event_row(
                event_id=violation.event_id,
                event_type="arb",
                condition_id=violation.condition_id,
                severity=violation.severity,
                actor="",
                direction=violation.direction,
                size_delta_usdc=None,
                prior_size_usdc=None,
                current_size_usdc=None,
                payload={
                    "relation_type": violation.relation_type,
                    "locked_profit_per_share": violation.locked_profit_per_share,
                    "implied_size": violation.implied_size,
                    "implied_profit_usdc": violation.implied_profit_usdc,
                    **violation.payload,
                },
                event_time=violation.event_time,
                observed_at=observed_at,
            )
            for violation in violations
        ]
        await ch.insert("signal_events", rows, column_names=SIGNAL_EVENTS_COLS)
        log.info("arb_checker.done", events=len(rows))
        return len(rows)
    finally:
        await ch.close()


async def run_forever() -> None:
    logging.basicConfig(level=get_settings().log_level)
    settings = get_settings()
    dlq = DeadLetterQueue(settings.redis_url, stream="arb_checker")
    try:
        while True:
            try:
                await run_once()
            except Exception as exc:
                log.error("arb_checker.failed", error=repr(exc))
                await dlq.push(
                    source="clickhouse",
                    reason="arb_checker_failed",
                    payload={"error": repr(exc)},
                )
            await asyncio.sleep(settings.arb_checker_interval_s)
    finally:
        await dlq.aclose()


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
