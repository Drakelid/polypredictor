"""Gamma discovery worker.

Every 60s (PRD §5.2.1): paginate ``/markets`` for each tag slug in scope
(``crypto``, ``finance``), upsert into ``markets_snapshots``, and update the
in-process :class:`MarketsRegistry` so the CLOB poller knows what to poll.

Implemented as an async loop; ``main()`` runs it as a standalone process.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging

import structlog
from model import classify
from polymarket_client import Market, PolymarketClient

from ..clickhouse import get_async_client
from ..dlq import DeadLetterQueue
from ..health import HealthSink
from ..markets_registry import MarketRef, MarketsRegistry
from ..settings import get_settings
from ..writers import (
    CLASSIFICATIONS_COLS,
    MARKETS_COLS,
    classification_row,
    market_row,
    utcnow,
)

log = structlog.get_logger(__name__)

# v1 scope (PRD §5.1).
TAG_SLUGS = ["crypto", "finance", "economics"]


def _classification_fingerprint(
    *,
    market_type: str,
    confidence: float,
    asset: str | None,
    strike: float | None,
    range_low: float | None,
    range_high: float | None,
    direction: str | None,
    event_family: str | None,
) -> str:
    """Stable digest of a classification. We only insert a new row when the
    digest changes vs the last-seen value for a condition_id — classifier
    re-runs against unchanged markets produce no writes, keeping the table thin.
    """
    payload = (
        f"{market_type}|{confidence:.2f}|{asset or ''}|{strike}|{range_low}|{range_high}|"
        f"{direction or ''}|{event_family or ''}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _multi_outcome_sibling_counts(markets: list[Market]) -> dict[str, int]:
    """Count active siblings per event_id for classifier hints."""
    counts: dict[str, int] = {}
    for market in markets:
        event_id = market.event_id or ""
        if not event_id:
            continue
        counts[event_id] = counts.get(event_id, 0) + 1
    return counts


async def run_once(
    pm: PolymarketClient,
    registry: MarketsRegistry,
    *,
    page_size: int = 100,
    classification_cache: dict[str, str] | None = None,
) -> int:
    """One discovery pass. Returns the number of markets upserted.

    ``classification_cache`` (condition_id → digest) de-duplicates classifier
    writes across runs. When absent, every classified market produces a row.
    """
    ch = await get_async_client()
    cache = classification_cache if classification_cache is not None else {}
    try:
        all_markets: dict[str, Market] = {}
        for tag in TAG_SLUGS:
            offset = 0
            while True:
                page = await pm.gamma.list_markets(
                    tag_slug=tag,
                    active=True,
                    closed=False,
                    limit=page_size,
                    offset=offset,
                )
                if not page:
                    break
                for m in page:
                    all_markets[m.condition_id] = m
                if len(page) < page_size:
                    break
                offset += page_size

        now = utcnow()
        rows = [market_row(m, observed_at=now) for m in all_markets.values()]
        if rows:
            await ch.insert("markets_snapshots", rows, column_names=MARKETS_COLS)

        # Classify and persist only changed labels. Multi-outcome markets rely
        # on event-level sibling counts from Gamma so the classifier can
        # distinguish "which/who wins?" event groups from ordinary binaries.
        sibling_counts = _multi_outcome_sibling_counts(list(all_markets.values()))
        classification_rows: list[tuple[object, ...]] = []
        for m in all_markets.values():
            result = classify(
                question=m.question,
                description=m.description,
                tags=list(m.tags),
                resolution_source=m.resolution_source,
                end_date=m.end_date,
                outcomes=None,
                multi_outcome_sibling_count=sibling_counts.get(m.event_id or "", 0),
            )
            f = result.features
            digest = _classification_fingerprint(
                market_type=result.market_type.value,
                confidence=result.confidence,
                asset=f.asset,
                strike=f.strike,
                range_low=f.range_low,
                range_high=f.range_high,
                direction=f.direction,
                event_family=f.event_family,
            )
            if cache.get(m.condition_id) == digest:
                continue
            cache[m.condition_id] = digest
            classification_rows.append(
                classification_row(
                    condition_id=m.condition_id,
                    market_type=result.market_type.value,
                    confidence=result.confidence,
                    needs_review=result.needs_review,
                    asset=f.asset,
                    strike=f.strike,
                    range_low=f.range_low,
                    range_high=f.range_high,
                    direction=f.direction,
                    event_family=f.event_family,
                    resolution_date=f.resolution_date,
                    reasons=result.reasons,
                    observed_at=now,
                )
            )
        if classification_rows:
            await ch.insert(
                "market_classifications",
                classification_rows,
                column_names=CLASSIFICATIONS_COLS,
            )

        refs = [
            MarketRef(
                condition_id=m.condition_id,
                token_ids=list(m.token_ids),
                volume_usdc=float(m.volume),
                liquidity_usdc=float(m.liquidity),
                active=m.active,
                closed=m.closed,
            )
            for m in all_markets.values()
        ]
        await registry.replace(refs)
        log.info(
            "gamma.discovery.done",
            markets=len(refs),
            classifications_written=len(classification_rows),
        )
        return len(refs)
    finally:
        await ch.close()


async def run_forever() -> None:
    logging.basicConfig(level=get_settings().log_level)
    s = get_settings()
    registry = MarketsRegistry()
    classification_cache: dict[str, str] = {}
    ch_for_health = await get_async_client()
    health = HealthSink(ch_for_health)
    dlq = DeadLetterQueue(s.redis_url, stream="gamma_discovery")
    try:
        async with PolymarketClient(
            gamma_base_url=s.polymarket_gamma_base,
            clob_base_url=s.polymarket_clob_base,
            data_base_url=s.polymarket_data_base,
            health_cb=health,
        ) as pm:
            while True:
                try:
                    await run_once(pm, registry, classification_cache=classification_cache)
                except Exception as exc:
                    log.error("gamma.discovery.failed", error=repr(exc))
                    await dlq.push(
                        source="polymarket.gamma",
                        reason="discovery_pass_failed",
                        payload={"error": repr(exc)},
                    )
                await asyncio.sleep(s.discover_interval_s)
    finally:
        await dlq.aclose()
        await ch_for_health.close()


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
