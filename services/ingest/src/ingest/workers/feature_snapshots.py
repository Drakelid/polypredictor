"""Periodic market-feature snapshot builder for M2.1.

The worker derives feature rows from the append-only PIT stores:

* latest quote -> mid, spread
* latest orderbook snapshot -> book imbalance at 1% / 5% depth
* quote history -> 1h / 24h / 7d momentum + 24h realized vol
* trade history -> informed taker flow, passive maker flow, decayed flow

Rows land in ``market_features`` with the usual ``observed_at`` PIT key so the
future ensemble can consume them without bypassing the store.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from ..clickhouse import get_async_client
from ..settings import get_settings
from ..writers import MARKET_FEATURES_COLS, market_features_row, utcnow


@dataclass(frozen=True)
class QuoteSnapshot:
    token_id: str
    condition_id: str
    mid: float
    best_bid: float
    best_ask: float
    spread: float
    observed_at: datetime


@dataclass(frozen=True)
class OrderbookLevel:
    side: str
    price: float
    size: float


@dataclass(frozen=True)
class TimedMid:
    observed_at: datetime
    mid: float


@dataclass(frozen=True)
class TimedTrade:
    observed_at: datetime
    size: float
    aggressor_side: str | None
    crossed_spread: bool | None


@dataclass(frozen=True)
class FeatureSnapshot:
    condition_id: str
    token_id: str
    mid: float
    spread: float
    book_imbalance_1pct: float | None
    book_imbalance_5pct: float | None
    momentum_1h: float | None
    momentum_24h: float | None
    momentum_7d: float | None
    realized_vol_24h: float | None
    informed_taker_flow_24h: float | None
    passive_maker_flow_24h: float | None
    decayed_directional_flow_24h: float | None


async def _latest_active_markets(
    ch: Any,
    *,
    observed_at: datetime,
    limit: int,
) -> list[tuple[str, str]]:
    query = """
        SELECT condition_id, token_ids, active, closed, archived
        FROM (
            SELECT condition_id, token_ids, active, closed, archived, volume_usdc
            FROM markets_snapshots
            WHERE observed_at <= {asof:DateTime64(3)}
            ORDER BY condition_id, observed_at DESC
            LIMIT 1 BY condition_id
        )
        ORDER BY volume_usdc DESC
        LIMIT {limit:UInt32}
    """
    result = await ch.query(query, parameters={"asof": observed_at, "limit": limit})
    rows: list[tuple[str, str]] = []
    for condition_id, token_ids, active, closed, archived in result.result_rows:
        if not bool(active) or bool(closed) or bool(archived):
            continue
        yes_token = str(token_ids[0]) if token_ids else ""
        if yes_token:
            rows.append((str(condition_id), yes_token))
    return rows


async def _latest_quotes(
    ch: Any,
    *,
    token_ids: list[str],
    observed_at: datetime,
) -> dict[str, QuoteSnapshot]:
    if not token_ids:
        return {}
    query = """
        SELECT token_id, condition_id, mid, best_bid, best_ask, spread, observed_at
        FROM market_quotes
        WHERE token_id IN {token_ids:Array(String)}
          AND observed_at <= {asof:DateTime64(3)}
        ORDER BY token_id, observed_at DESC
        LIMIT 1 BY token_id
    """
    result = await ch.query(
        query,
        parameters={"token_ids": token_ids, "asof": observed_at},
    )
    return {
        str(row[0]): QuoteSnapshot(
            token_id=str(row[0]),
            condition_id=str(row[1]),
            mid=float(row[2]),
            best_bid=float(row[3]),
            best_ask=float(row[4]),
            spread=float(row[5]),
            observed_at=row[6],
        )
        for row in result.result_rows
    }


async def _latest_orderbooks(
    ch: Any,
    *,
    token_ids: list[str],
    observed_at: datetime,
) -> dict[str, list[OrderbookLevel]]:
    if not token_ids:
        return {}
    query = """
        WITH latest_books AS (
            SELECT token_id, max(observed_at) AS latest_observed_at
            FROM orderbook_snapshots
            WHERE token_id IN {token_ids:Array(String)}
              AND observed_at <= {asof:DateTime64(3)}
              AND source = 'rest'
            GROUP BY token_id
        )
        SELECT ob.token_id, ob.side, ob.price, ob.size
        FROM orderbook_snapshots AS ob
        INNER JOIN latest_books AS lb
            ON ob.token_id = lb.token_id
           AND ob.observed_at = lb.latest_observed_at
        WHERE ob.source = 'rest'
        ORDER BY ob.token_id, ob.side, ob.price DESC
    """
    result = await ch.query(
        query,
        parameters={"token_ids": token_ids, "asof": observed_at},
    )
    out: dict[str, list[OrderbookLevel]] = defaultdict(list)
    for token_id, side, price, size in result.result_rows:
        out[str(token_id)].append(
            OrderbookLevel(
                side=str(side),
                price=float(price),
                size=float(size),
            )
        )
    return dict(out)


async def _latest_mid_before(
    ch: Any,
    *,
    token_ids: list[str],
    cutoff: datetime,
) -> dict[str, float]:
    if not token_ids:
        return {}
    query = """
        SELECT token_id, mid
        FROM market_quotes
        WHERE token_id IN {token_ids:Array(String)}
          AND observed_at <= {cutoff:DateTime64(3)}
        ORDER BY token_id, observed_at DESC
        LIMIT 1 BY token_id
    """
    result = await ch.query(
        query,
        parameters={"token_ids": token_ids, "cutoff": cutoff},
    )
    return {str(token_id): float(mid) for token_id, mid in result.result_rows}


async def _quote_history(
    ch: Any,
    *,
    token_ids: list[str],
    observed_at: datetime,
    lookback: timedelta,
) -> dict[str, list[TimedMid]]:
    if not token_ids:
        return {}
    query = """
        SELECT token_id, observed_at, mid
        FROM market_quotes
        WHERE token_id IN {token_ids:Array(String)}
          AND observed_at >= {start:DateTime64(3)}
          AND observed_at <= {asof:DateTime64(3)}
        ORDER BY token_id, observed_at ASC
    """
    start = observed_at - lookback
    result = await ch.query(
        query,
        parameters={"token_ids": token_ids, "start": start, "asof": observed_at},
    )
    out: dict[str, list[TimedMid]] = defaultdict(list)
    for token_id, ts, mid in result.result_rows:
        out[str(token_id)].append(TimedMid(observed_at=ts, mid=float(mid)))
    return dict(out)


async def _trade_history(
    ch: Any,
    *,
    token_ids: list[str],
    observed_at: datetime,
    lookback: timedelta,
) -> dict[str, list[TimedTrade]]:
    if not token_ids:
        return {}
    query = """
        SELECT token_id, observed_at, size, aggressor_side, crossed_spread
        FROM trades_raw
        WHERE token_id IN {token_ids:Array(String)}
          AND observed_at >= {start:DateTime64(3)}
          AND observed_at <= {asof:DateTime64(3)}
        ORDER BY token_id, observed_at ASC
    """
    start = observed_at - lookback
    result = await ch.query(
        query,
        parameters={"token_ids": token_ids, "start": start, "asof": observed_at},
    )
    out: dict[str, list[TimedTrade]] = defaultdict(list)
    for token_id, ts, size, aggressor_side, crossed_spread in result.result_rows:
        out[str(token_id)].append(
            TimedTrade(
                observed_at=ts,
                size=float(size),
                aggressor_side=str(aggressor_side) if aggressor_side is not None else None,
                crossed_spread=(bool(crossed_spread) if crossed_spread is not None else None),
            )
        )
    return dict(out)


def _momentum(current_mid: float, past_mid: float | None) -> float | None:
    if past_mid is None or past_mid <= 0:
        return None
    return (current_mid / past_mid) - 1.0


def _book_imbalance(
    levels: list[OrderbookLevel],
    *,
    best_bid: float,
    best_ask: float,
    depth_pct: float,
) -> float | None:
    if best_bid <= 0 or best_ask <= 0 or best_ask < best_bid:
        return None
    min_bid = best_bid * (1.0 - depth_pct)
    max_ask = best_ask * (1.0 + depth_pct)
    bid_depth = sum(level.size for level in levels if level.side == "bid" and level.price >= min_bid)
    ask_depth = sum(level.size for level in levels if level.side == "ask" and level.price <= max_ask)
    total = bid_depth + ask_depth
    if total <= 0:
        return None
    return (bid_depth - ask_depth) / total


def _annualized_realized_vol(points: list[TimedMid]) -> float | None:
    if len(points) < 2:
        return None
    total_dt = 0.0
    sum_sq = 0.0
    prev = points[0]
    for point in points[1:]:
        if prev.mid <= 0 or point.mid <= 0:
            prev = point
            continue
        dt = (point.observed_at - prev.observed_at).total_seconds()
        if dt <= 0:
            prev = point
            continue
        r = math.log(point.mid / prev.mid)
        sum_sq += r * r
        total_dt += dt
        prev = point
    if total_dt <= 0:
        return None
    seconds_per_year = 365.0 * 24.0 * 3600.0
    return math.sqrt((sum_sq / total_dt) * seconds_per_year)


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    idx = (len(ordered) - 1) * q
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return ordered[lo]
    frac = idx - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


def _trade_sign(aggressor_side: str | None) -> float | None:
    if aggressor_side == "buy":
        return 1.0
    if aggressor_side == "sell":
        return -1.0
    return None


def _trade_flow_features(
    trades: list[TimedTrade],
    *,
    observed_at: datetime,
    decay_half_life_h: float,
) -> tuple[float | None, float | None, float | None]:
    if not trades:
        return (None, None, None)
    sizes = [trade.size for trade in trades if trade.size > 0]
    size_p80 = _percentile(sizes, 0.8)
    known_sign_trades = [trade for trade in trades if _trade_sign(trade.aggressor_side) is not None]
    total_known_size = sum(trade.size for trade in known_sign_trades)

    informed_num = 0.0
    passive_num = 0.0
    decayed_num = 0.0
    decayed_den = 0.0
    half_life_s = max(decay_half_life_h, 0.1) * 3600.0

    for trade in known_sign_trades:
        sign = _trade_sign(trade.aggressor_side)
        assert sign is not None
        if size_p80 is not None and trade.crossed_spread is True and trade.size >= size_p80:
            informed_num += sign * trade.size
        if trade.crossed_spread is False:
            passive_num += (-sign) * trade.size
        age_s = max(0.0, (observed_at - trade.observed_at).total_seconds())
        weight = math.exp(-math.log(2.0) * age_s / half_life_s)
        decayed_num += sign * trade.size * weight
        decayed_den += trade.size * weight

    informed = (informed_num / total_known_size) if total_known_size > 0 else None
    passive = (passive_num / total_known_size) if total_known_size > 0 else None
    decayed = (decayed_num / decayed_den) if decayed_den > 0 else None
    return (informed, passive, decayed)


async def build_feature_snapshots(
    ch: Any,
    *,
    observed_at: datetime,
    batch_size: int,
    decay_half_life_h: float,
) -> list[FeatureSnapshot]:
    markets = await _latest_active_markets(ch, observed_at=observed_at, limit=batch_size)
    condition_to_token = {condition_id: token_id for condition_id, token_id in markets}
    token_ids = list(condition_to_token.values())
    quotes = await _latest_quotes(ch, token_ids=token_ids, observed_at=observed_at)
    orderbooks = await _latest_orderbooks(ch, token_ids=token_ids, observed_at=observed_at)
    mids_1h = await _latest_mid_before(ch, token_ids=token_ids, cutoff=observed_at - timedelta(hours=1))
    mids_24h = await _latest_mid_before(ch, token_ids=token_ids, cutoff=observed_at - timedelta(hours=24))
    mids_7d = await _latest_mid_before(ch, token_ids=token_ids, cutoff=observed_at - timedelta(days=7))
    quote_history = await _quote_history(
        ch,
        token_ids=token_ids,
        observed_at=observed_at,
        lookback=timedelta(hours=24),
    )
    trade_history = await _trade_history(
        ch,
        token_ids=token_ids,
        observed_at=observed_at,
        lookback=timedelta(hours=24),
    )

    out: list[FeatureSnapshot] = []
    for condition_id, token_id in markets:
        quote = quotes.get(token_id)
        if quote is None:
            continue
        levels = orderbooks.get(token_id, [])
        informed, passive, decayed = _trade_flow_features(
            trade_history.get(token_id, []),
            observed_at=observed_at,
            decay_half_life_h=decay_half_life_h,
        )
        out.append(
            FeatureSnapshot(
                condition_id=condition_id,
                token_id=token_id,
                mid=quote.mid,
                spread=quote.spread,
                book_imbalance_1pct=_book_imbalance(
                    levels,
                    best_bid=quote.best_bid,
                    best_ask=quote.best_ask,
                    depth_pct=0.01,
                ),
                book_imbalance_5pct=_book_imbalance(
                    levels,
                    best_bid=quote.best_bid,
                    best_ask=quote.best_ask,
                    depth_pct=0.05,
                ),
                momentum_1h=_momentum(quote.mid, mids_1h.get(token_id)),
                momentum_24h=_momentum(quote.mid, mids_24h.get(token_id)),
                momentum_7d=_momentum(quote.mid, mids_7d.get(token_id)),
                realized_vol_24h=_annualized_realized_vol(quote_history.get(token_id, [])),
                informed_taker_flow_24h=informed,
                passive_maker_flow_24h=passive,
                decayed_directional_flow_24h=decayed,
            )
        )
    return out


async def run_once() -> int:
    settings = get_settings()
    ch = await get_async_client()
    try:
        observed_at = utcnow()
        snapshots = await build_feature_snapshots(
            ch,
            observed_at=observed_at,
            batch_size=settings.feature_snapshots_batch_size,
            decay_half_life_h=settings.feature_snapshots_trade_decay_half_life_h,
        )
        if snapshots:
            rows = [
                market_features_row(
                    condition_id=snapshot.condition_id,
                    token_id=snapshot.token_id,
                    mid=snapshot.mid,
                    spread=snapshot.spread,
                    book_imbalance_1pct=snapshot.book_imbalance_1pct,
                    book_imbalance_5pct=snapshot.book_imbalance_5pct,
                    momentum_1h=snapshot.momentum_1h,
                    momentum_24h=snapshot.momentum_24h,
                    momentum_7d=snapshot.momentum_7d,
                    realized_vol_24h=snapshot.realized_vol_24h,
                    informed_taker_flow_24h=snapshot.informed_taker_flow_24h,
                    passive_maker_flow_24h=snapshot.passive_maker_flow_24h,
                    decayed_directional_flow_24h=snapshot.decayed_directional_flow_24h,
                    observed_at=observed_at,
                )
                for snapshot in snapshots
            ]
            await ch.insert("market_features", rows, column_names=MARKET_FEATURES_COLS)
        return len(snapshots)
    finally:
        await ch.close()


async def run_forever() -> None:
    logging.basicConfig(level=get_settings().log_level)
    settings = get_settings()
    while True:
        await run_once()
        await asyncio.sleep(settings.feature_snapshots_interval_s)


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
