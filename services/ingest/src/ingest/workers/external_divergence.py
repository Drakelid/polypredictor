"""External-venue divergence checker for M3.4.

Current scope: crypto threshold markets where we have enough structure to
compare Polymarket against external venues.

For each active threshold market on BTC/ETH/SOL, the worker computes:

* Deribit options-implied terminal probability
* Binance perpetual-basis-implied probability

If either diverges from the market YES mid by at least the configured gap, we
emit an ``external_divergence`` signal event with the raw venue inputs.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import datetime

import structlog
from model import (
    BinancePerpClient,
    BinancePerpSnapshot,
    DeribitClient,
    MarketType,
    options_implied_probability,
    perp_basis_implied_probability,
)

from ..clickhouse import get_async_client
from ..dlq import DeadLetterQueue
from ..settings import get_settings
from ..writers import SIGNAL_EVENTS_COLS, signal_event_row, utcnow

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ThresholdCandidate:
    condition_id: str
    question: str
    token_id: str
    market_mid: float
    asset: str
    strike: float
    direction: str
    resolution_date: datetime


async def _latest_threshold_candidates(
    ch: object,
    *,
    asked_at: datetime,
    limit: int,
) -> list[ThresholdCandidate]:
    market_query = """
        SELECT condition_id, question, token_ids, active, closed, archived
        FROM (
            SELECT condition_id, question, token_ids, active, closed, archived
            FROM markets_snapshots
            WHERE observed_at <= {asof:DateTime64(3)}
            ORDER BY condition_id, observed_at DESC
            LIMIT 1 BY condition_id
        )
        ORDER BY condition_id
        LIMIT {limit:UInt32}
    """
    market_result = await ch.query(  # type: ignore[attr-defined]
        market_query,
        parameters={"asof": asked_at, "limit": limit},
    )
    snapshots: dict[str, tuple[str, str]] = {}
    token_ids: list[str] = []
    for row in market_result.result_rows:
        if not bool(row[3]) or bool(row[4]) or bool(row[5]):
            continue
        condition_id = str(row[0])
        question = str(row[1])
        toks = list(row[2]) if row[2] else []
        if not toks:
            continue
        yes_token = str(toks[0])
        snapshots[condition_id] = (question, yes_token)
        token_ids.append(yes_token)

    if not snapshots:
        return []

    class_query = """
        SELECT condition_id, market_type, asset, strike, direction, resolution_date
        FROM market_classifications
        WHERE condition_id IN {conds:Array(String)}
          AND observed_at <= {asof:DateTime64(3)}
        ORDER BY condition_id, observed_at DESC
        LIMIT 1 BY condition_id
    """
    class_result = await ch.query(  # type: ignore[attr-defined]
        class_query,
        parameters={"conds": list(snapshots.keys()), "asof": asked_at},
    )
    classifications = {
        str(row[0]): row for row in class_result.result_rows
        if str(row[1]) == MarketType.THRESHOLD.value
    }

    quote_query = """
        SELECT token_id, mid
        FROM market_quotes
        WHERE token_id IN {toks:Array(String)}
          AND observed_at <= {asof:DateTime64(3)}
        ORDER BY token_id, observed_at DESC
        LIMIT 1 BY token_id
    """
    quote_result = await ch.query(  # type: ignore[attr-defined]
        quote_query,
        parameters={"toks": token_ids, "asof": asked_at},
    )
    mids = {str(row[0]): float(row[1]) for row in quote_result.result_rows}

    out: list[ThresholdCandidate] = []
    for condition_id, (question, yes_token) in snapshots.items():
        classification = classifications.get(condition_id)
        if classification is None:
            continue
        market_mid = mids.get(yes_token)
        if market_mid is None:
            continue
        asset = str(classification[2] or "")
        strike = float(classification[3]) if classification[3] is not None else 0.0
        direction = str(classification[4] or "")
        resolution_date = classification[5]
        if asset not in {"BTC", "ETH", "SOL"}:
            continue
        if strike <= 0.0 or direction not in {"above", "below"} or resolution_date is None:
            continue
        out.append(
            ThresholdCandidate(
                condition_id=condition_id,
                question=question,
                token_id=yes_token,
                market_mid=market_mid,
                asset=asset,
                strike=strike,
                direction=direction,
                resolution_date=resolution_date,  # type: ignore[arg-type]
            )
        )
    return out


def _binance_symbol(asset: str) -> str | None:
    return {
        "BTC": "BTCUSDT",
        "ETH": "ETHUSDT",
        "SOL": "SOLUSDT",
    }.get(asset)


def _event_id(*, condition_id: str, observed_at: datetime) -> str:
    seed = f"{condition_id}|external_divergence|{observed_at.isoformat()}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


def _direction_from_probs(
    market_mid: float,
    options_prob: float | None,
    basis_prob: float | None,
) -> str:
    probs = [prob for prob in (options_prob, basis_prob) if prob is not None]
    if not probs:
        return "neutral"
    avg = sum(probs) / len(probs)
    if avg > market_mid:
        return "yes"
    if avg < market_mid:
        return "no"
    return "neutral"


def _severity(
    market_mid: float,
    options_prob: float | None,
    basis_prob: float | None,
) -> float:
    gaps = [abs(prob - market_mid) for prob in (options_prob, basis_prob) if prob is not None]
    return (max(gaps) if gaps else 0.0) * 10_000.0


async def run_once(
    *,
    deribit: DeribitClient | None = None,
    binance: BinancePerpClient | None = None,
) -> int:
    settings = get_settings()
    observed_at = utcnow()
    ch = await get_async_client()
    try:
        candidates = await _latest_threshold_candidates(
            ch,
            asked_at=observed_at,
            limit=settings.external_divergence_max_markets,
        )
        if not candidates:
            return 0

        async with AsyncExitStack() as stack:
            if deribit is None:
                deribit = await stack.enter_async_context(
                    DeribitClient(base_url=settings.deribit_base)
                )
            if binance is None:
                binance = await stack.enter_async_context(
                    BinancePerpClient(base_url=settings.binance_base)
                )

            perp_snapshots: dict[str, BinancePerpSnapshot | None] = {}
            rows: list[tuple[object, ...]] = []
            for candidate in candidates:
                horizon_days = (
                    candidate.resolution_date - observed_at
                ).total_seconds() / 86_400.0
                if horizon_days <= 0.0:
                    continue
                iv = await deribit.fetch_iv(
                    currency=candidate.asset,
                    strike=candidate.strike,
                    direction=candidate.direction,
                    horizon_days=horizon_days,
                )
                if iv is None:
                    continue
                symbol = _binance_symbol(candidate.asset)
                if symbol is None:
                    continue
                if symbol not in perp_snapshots:
                    perp_snapshots[symbol] = await binance.premium_index(symbol=symbol)
                perp_snapshot = perp_snapshots[symbol]
                options_prob = options_implied_probability(
                    strike=candidate.strike,
                    direction=candidate.direction,
                    resolution_date=candidate.resolution_date,
                    asked_at=observed_at,
                    iv=iv,
                )
                sigma = iv.strike_iv if iv.strike_iv > 0 else iv.atm_iv
                basis_prob = (
                    perp_basis_implied_probability(
                        strike=candidate.strike,
                        direction=candidate.direction,
                        resolution_date=candidate.resolution_date,
                        asked_at=observed_at,
                        sigma=sigma,
                        snapshot=perp_snapshot,
                    )
                    if perp_snapshot is not None
                    else None
                )
                option_gap = (
                    abs(options_prob - candidate.market_mid)
                    if options_prob is not None
                    else None
                )
                basis_gap = (
                    abs(basis_prob - candidate.market_mid)
                    if basis_prob is not None
                    else None
                )
                if (
                    (option_gap is None or option_gap < settings.external_divergence_min_gap)
                    and (basis_gap is None or basis_gap < settings.external_divergence_min_gap)
                ):
                    continue
                rows.append(
                    signal_event_row(
                        event_id=_event_id(
                            condition_id=candidate.condition_id,
                            observed_at=observed_at,
                        ),
                        event_type="external_divergence",
                        condition_id=candidate.condition_id,
                        severity=_severity(
                            candidate.market_mid,
                            options_prob,
                            basis_prob,
                        ),
                        actor="deribit+binance",
                        direction=_direction_from_probs(
                            candidate.market_mid,
                            options_prob,
                            basis_prob,
                        ),
                        size_delta_usdc=None,
                        prior_size_usdc=None,
                        current_size_usdc=None,
                        payload={
                            "asset": candidate.asset,
                            "question": candidate.question,
                            "market_mid": candidate.market_mid,
                            "strike": candidate.strike,
                            "direction": candidate.direction,
                            "resolution_date": candidate.resolution_date.isoformat(),
                            "options_prob": options_prob,
                            "options_gap": option_gap,
                            "perp_basis_prob": basis_prob,
                            "perp_basis_gap": basis_gap,
                            "deribit_index_price": iv.index_price,
                            "deribit_atm_iv": iv.atm_iv,
                            "deribit_strike_iv": iv.strike_iv,
                            "binance_symbol": symbol,
                            "binance_index_price": (
                                perp_snapshot.index_price if perp_snapshot is not None else None
                            ),
                            "binance_mark_price": (
                                perp_snapshot.mark_price if perp_snapshot is not None else None
                            ),
                            "binance_funding_rate": (
                                perp_snapshot.funding_rate if perp_snapshot is not None else None
                            ),
                        },
                        event_time=observed_at,
                        observed_at=observed_at,
                    )
                )
        if not rows:
            log.info("external_divergence.done", events=0)
            return 0
        await ch.insert("signal_events", rows, column_names=SIGNAL_EVENTS_COLS)
        log.info("external_divergence.done", events=len(rows))
        return len(rows)
    finally:
        await ch.close()


async def run_forever() -> None:
    logging.basicConfig(level=get_settings().log_level)
    settings = get_settings()
    dlq = DeadLetterQueue(settings.redis_url, stream="external_divergence")
    try:
        while True:
            try:
                await run_once()
            except Exception as exc:
                log.error("external_divergence.failed", error=repr(exc))
                await dlq.push(
                    source="external.crosscheck",
                    reason="external_divergence_failed",
                    payload={"error": repr(exc)},
                )
            await asyncio.sleep(settings.external_divergence_interval_s)
    finally:
        await dlq.aclose()


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
