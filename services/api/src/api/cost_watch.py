"""Cost-watch dashboard joining ``ingest_health`` request counts with
operator-configured per-1000-request unit prices (PRD §9 cross-cutting).

This is the I/O wrapper around the pure helper in
``packages/model/src/model/cost_watch.py``: the ingestion path already logs
every outbound HTTP request into ``ingest_health``, the API layer aggregates
those counts in :func:`source_health_summary_asof`, and this module turns the
result into a per-provider running spend.

Default unit prices are 0 — free sources stay at $0, paid sources only
register cost once an operator sets the relevant env (`COST_PER_1K_REQUESTS_*`).
The endpoint is read-only and does not block on missing prices.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from clickhouse_connect.driver.asyncclient import AsyncClient
from model import estimate_api_cost

from .settings import Settings, get_settings
from .source_health import SourceHealthSummary, source_health_summary_asof


@dataclass(frozen=True)
class CostWatchProvider:
    provider: str
    sources: tuple[str, ...]
    request_count: int
    cost_per_1k_requests: float
    cost_usd: float


@dataclass(frozen=True)
class CostWatchReport:
    asked_at: datetime
    lookback_hours: int
    providers: list[CostWatchProvider]
    total_cost_usd: float
    unmapped_sources: list[str]


# Logical provider -> the list of `ingest_health.source` strings emitted by
# the workers that hit that provider's API. Keeping the mapping explicit
# means a new ingest source either lands in a known bucket (because it
# reused the existing source string) or shows up in `unmapped_sources` so
# the operator notices.
_PROVIDER_SOURCE_MAP: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("x_api", ("x", "x.api", "twitter", "twitter.api")),
    ("glassnode", ("glassnode",)),
    ("dune", ("dune",)),
    ("cme_fedwatch", ("cme.fedwatch", "cme_fedwatch")),
    ("deribit", ("deribit",)),
    (
        "polymarket",
        (
            "polymarket.gamma",
            "polymarket.clob",
            "polymarket.data",
            "polymarket.wss",
        ),
    ),
)

_FREE_SOURCES = frozenset(
    {
        "fred",
        "bls",
        "binance.spot",
        "binance.perp",
        "coinbase.intx",
        "coinbase.spot",
        "coingecko.spot",
        "reddit",
        "rest",
        "clickhouse",
        "microstructure.signals",
    }
)


def _settings_unit_prices(settings: Settings) -> dict[str, float]:
    """Pull the operator-configured cost-per-1k-requests for each provider."""
    return {
        "x_api": float(settings.cost_per_1k_requests_x_api),
        "glassnode": float(settings.cost_per_1k_requests_glassnode),
        "dune": float(settings.cost_per_1k_requests_dune),
        "cme_fedwatch": float(settings.cost_per_1k_requests_cme_fedwatch),
        "deribit": float(settings.cost_per_1k_requests_deribit),
        "polymarket": float(settings.cost_per_1k_requests_polymarket),
    }


def _provider_for_source(source: str) -> str | None:
    for provider, source_set in _PROVIDER_SOURCE_MAP:
        if source in source_set:
            return provider
    if source in _FREE_SOURCES or source.startswith("rss:"):
        return None
    return None


def _aggregate_by_provider(
    summaries: list[SourceHealthSummary],
    unit_prices: Mapping[str, float],
) -> tuple[list[CostWatchProvider], list[str]]:
    """Roll source-level counts into provider-level counts and apply pricing.

    Returns ``(providers_in_map_order, unmapped_sources)``. Unmapped sources
    are surfaced so an operator can either (a) add a unit price + entry to
    the provider map, or (b) explicitly ack them as free in ``_FREE_SOURCES``.
    """
    counts: dict[str, int] = {}
    sources_seen: dict[str, list[str]] = {}
    unmapped: list[str] = []

    for summary in summaries:
        provider = _provider_for_source(summary.source)
        if provider is None:
            if summary.source in _FREE_SOURCES or summary.source.startswith("rss:"):
                continue
            unmapped.append(summary.source)
            continue
        counts[provider] = counts.get(provider, 0) + summary.total_requests
        sources_seen.setdefault(provider, []).append(summary.source)

    # `estimate_api_cost` operates on raw call counts times per-call prices;
    # convert the operator-facing per-1k-requests into per-call here.
    per_call_prices = {
        provider: float(unit_prices.get(provider, 0.0)) / 1000.0
        for provider in counts
    }
    _, breakdown = estimate_api_cost(counts, per_call_prices)
    providers: list[CostWatchProvider] = []
    for provider, _sources in _PROVIDER_SOURCE_MAP:
        if provider not in counts:
            continue
        providers.append(
            CostWatchProvider(
                provider=provider,
                sources=tuple(sorted(set(sources_seen.get(provider, [])))),
                request_count=counts[provider],
                cost_per_1k_requests=float(unit_prices.get(provider, 0.0)),
                cost_usd=float(breakdown.get(provider, 0.0)),
            )
        )
    return providers, sorted(set(unmapped))


async def cost_watch_report(
    ch: AsyncClient,
    *,
    asked_at: datetime,
    lookback_hours: int | None = None,
    unit_prices: Mapping[str, float] | None = None,
) -> CostWatchReport:
    settings = get_settings()
    window = int(lookback_hours if lookback_hours is not None else settings.cost_watch_lookback_hours)
    if window <= 0:
        raise ValueError("lookback_hours must be positive")

    summaries = await source_health_summary_asof(
        ch,
        asked_at=asked_at,
        lookback_hours=window,
    )
    prices = dict(unit_prices) if unit_prices is not None else _settings_unit_prices(settings)
    providers, unmapped = _aggregate_by_provider(summaries, prices)
    total = sum(provider.cost_usd for provider in providers)
    return CostWatchReport(
        asked_at=asked_at,
        lookback_hours=window,
        providers=providers,
        total_cost_usd=total,
        unmapped_sources=unmapped,
    )


__all__ = [
    "CostWatchProvider",
    "CostWatchReport",
    "cost_watch_report",
]
