"""Row-shaping helpers for ClickHouse inserts.

Keeping these in one place means the ClickHouse column order is the single
source of truth; workers produce ``tuple`` rows via these helpers rather than
open-coding column orders at each callsite.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from model import SpotTickerSnapshot
from polymarket_client import HolderEntry, Market, Position, PriceHistoryBucket

# -- markets_snapshots --------------------------------------------------------

MARKETS_COLS = (
    "condition_id",
    "event_id",
    "question",
    "description",
    "slug",
    "category",
    "tags",
    "active",
    "closed",
    "archived",
    "volume_usdc",
    "liquidity_usdc",
    "open_interest_usdc",
    "end_date",
    "resolution_source",
    "token_ids",
    "event_time",
    "observed_at",
    "payload_hash",
)


def market_row(m: Market, *, observed_at: datetime) -> tuple[Any, ...]:
    """Shape a :class:`Market` into a ``markets_snapshots`` row."""
    event_time = observed_at
    event_id = m.event_id or ""
    payload_hash = hashlib.sha256(
        json.dumps(
            {
                "cid": m.condition_id,
                "eid": event_id,
                "q": m.question,
                "active": m.active,
                "closed": m.closed,
                "vol": m.volume,
                "liq": m.liquidity,
                "oi": m.open_interest,
                "tokens": m.token_ids,
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return (
        m.condition_id,
        event_id,
        m.question,
        m.description or "",
        m.slug,
        m.category or "",
        list(m.tags),
        1 if m.active else 0,
        1 if m.closed else 0,
        1 if m.archived else 0,
        float(m.volume),
        float(m.liquidity),
        float(m.open_interest),
        m.end_date,
        m.resolution_source or "",
        list(m.token_ids),
        event_time,
        observed_at,
        payload_hash,
    )


# -- market_quotes ------------------------------------------------------------

QUOTES_COLS = (
    "token_id",
    "condition_id",
    "mid",
    "best_bid",
    "best_ask",
    "bid_size",
    "ask_size",
    "spread",
    "event_time",
    "observed_at",
)


def quote_row(
    *,
    token_id: str,
    condition_id: str,
    mid: float,
    best_bid: float,
    best_ask: float,
    bid_size: float,
    ask_size: float,
    observed_at: datetime,
) -> tuple[Any, ...]:
    spread = max(0.0, best_ask - best_bid)
    return (
        token_id,
        condition_id,
        mid,
        best_bid,
        best_ask,
        bid_size,
        ask_size,
        spread,
        observed_at,
        observed_at,
    )


# -- orderbook_snapshots ------------------------------------------------------

ORDERBOOK_COLS = (
    "token_id",
    "condition_id",
    "side",
    "price",
    "size",
    "event_time",
    "observed_at",
    "source",
    "sequence",
)


def orderbook_rows(
    *,
    token_id: str,
    condition_id: str,
    bids: list[tuple[float, float]],
    asks: list[tuple[float, float]],
    observed_at: datetime,
    source: str = "rest",
    sequence: int = 0,
) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    for p, s in bids:
        rows.append(
            (token_id, condition_id, "bid", float(p), float(s), observed_at, observed_at, source, sequence)
        )
    for p, s in asks:
        rows.append(
            (token_id, condition_id, "ask", float(p), float(s), observed_at, observed_at, source, sequence)
        )
    return rows


# -- trades_raw ---------------------------------------------------------------

TRADES_COLS = (
    "trade_id",
    "token_id",
    "condition_id",
    "price",
    "size",
    "side",
    "aggressor_side",
    "crossed_spread",
    "event_time",
    "observed_at",
)


def trade_row(
    *,
    trade_id: str,
    token_id: str,
    condition_id: str,
    price: float,
    size: float,
    side: str,
    aggressor_side: str | None,
    crossed_spread: bool | None,
    event_time: datetime,
    observed_at: datetime,
) -> tuple[Any, ...]:
    return (
        trade_id,
        token_id,
        condition_id,
        float(price),
        float(size),
        side.lower(),
        aggressor_side.lower() if aggressor_side else None,
        None if crossed_spread is None else (1 if crossed_spread else 0),
        event_time,
        observed_at,
    )


# -- prices_history -----------------------------------------------------------

PRICES_HISTORY_COLS = (
    "token_id",
    "condition_id",
    "interval_bucket",
    "bucket_start",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "event_time",
    "observed_at",
)


def price_history_rows(
    *,
    token_id: str,
    condition_id: str,
    interval_bucket: str,
    history: list[PriceHistoryBucket],
    observed_at: datetime,
) -> list[tuple[Any, ...]]:
    """Shape ``/prices-history`` buckets into ``prices_history`` rows.

    The public CLOB endpoint currently exposes bucket timestamps plus a single
    price field. Until Polymarket publishes full OHLCV, we canonicalize that
    price into open/high/low/close and leave volume at 0.0. This keeps the PIT
    history populated for charts and feature bootstrapping without inventing
    data we don't have.
    """
    rows: list[tuple[Any, ...]] = []
    for bucket in history:
        bucket_start = datetime.fromtimestamp(bucket.t, tz=UTC)
        price = float(bucket.p)
        rows.append(
            (
                token_id,
                condition_id,
                interval_bucket,
                bucket_start,
                price,
                price,
                price,
                price,
                0.0,
                bucket_start,
                observed_at,
            )
        )
    return rows


# -- positions_smart_money ----------------------------------------------------

SMART_MONEY_POSITIONS_COLS = (
    "proxy_wallet",
    "condition_id",
    "token_id",
    "outcome",
    "size",
    "avg_entry_price",
    "current_value_usdc",
    "leaderboard_rank",
    "leaderboard_pnl",
    "leaderboard_vol",
    "event_time",
    "observed_at",
)


def smart_money_position_row(
    *,
    position: Position,
    leaderboard_rank: int | None,
    leaderboard_pnl: float | None,
    leaderboard_vol: float | None,
    observed_at: datetime,
) -> tuple[Any, ...]:
    return (
        position.proxy_wallet,
        position.condition_id,
        position.token_id,
        position.outcome.upper(),
        float(position.size),
        float(position.avg_price),
        float(position.current_value),
        leaderboard_rank,
        leaderboard_pnl,
        leaderboard_vol,
        observed_at,
        observed_at,
    )


# -- holders_snapshots --------------------------------------------------------

HOLDERS_COLS = (
    "condition_id",
    "token_id",
    "rank",
    "proxy_wallet",
    "size",
    "pct_of_outcome",
    "event_time",
    "observed_at",
)


def holder_row(
    *,
    condition_id: str,
    token_id: str,
    rank: int,
    holder: HolderEntry,
    pct_of_outcome: float,
    observed_at: datetime,
) -> tuple[Any, ...]:
    return (
        condition_id,
        token_id,
        int(rank),
        holder.proxy_wallet,
        float(holder.size),
        float(pct_of_outcome),
        observed_at,
        observed_at,
    )


# -- market_concentration -----------------------------------------------------

MARKET_CONCENTRATION_COLS = (
    "condition_id",
    "yes_gini",
    "no_gini",
    "yes_top1_pct",
    "no_top1_pct",
    "yes_top5_pct",
    "no_top5_pct",
    "yes_whale_flag",
    "no_whale_flag",
    "any_whale_flag",
    "yes_holders_count",
    "no_holders_count",
    "max_gini",
    "event_time",
    "observed_at",
)


def market_concentration_row(
    *,
    condition_id: str,
    yes_gini: float | None,
    no_gini: float | None,
    yes_top1_pct: float | None,
    no_top1_pct: float | None,
    yes_top5_pct: float | None,
    no_top5_pct: float | None,
    yes_whale_flag: bool,
    no_whale_flag: bool,
    yes_holders_count: int,
    no_holders_count: int,
    observed_at: datetime,
) -> tuple[Any, ...]:
    max_gini = None
    ginis = [value for value in (yes_gini, no_gini) if value is not None]
    if ginis:
        max_gini = max(ginis)
    any_whale = yes_whale_flag or no_whale_flag
    return (
        condition_id,
        yes_gini,
        no_gini,
        yes_top1_pct,
        no_top1_pct,
        yes_top5_pct,
        no_top5_pct,
        1 if yes_whale_flag else 0,
        1 if no_whale_flag else 0,
        1 if any_whale else 0,
        int(yes_holders_count),
        int(no_holders_count),
        max_gini,
        observed_at,
        observed_at,
    )


# -- smart_money_qualifications ----------------------------------------------

SMART_MONEY_QUALIFICATIONS_COLS = (
    "proxy_wallet",
    "qualified",
    "reasons",
    "distinct_markets",
    "leaderboard_rank",
    "leaderboard_pnl",
    "leaderboard_vol",
    "sources",
    "appeared_in_pnl",
    "appeared_in_vol",
    "min_volume_ok",
    "event_time",
    "observed_at",
)


def smart_money_qualification_row(
    *,
    proxy_wallet: str,
    qualified: bool,
    reasons: list[str],
    distinct_markets: int,
    leaderboard_rank: int | None,
    leaderboard_pnl: float | None,
    leaderboard_vol: float | None,
    sources: list[str],
    appeared_in_pnl: bool,
    appeared_in_vol: bool,
    min_volume_ok: bool,
    observed_at: datetime,
) -> tuple[Any, ...]:
    return (
        proxy_wallet,
        1 if qualified else 0,
        list(reasons),
        int(distinct_markets),
        leaderboard_rank,
        leaderboard_pnl,
        leaderboard_vol,
        list(sources),
        1 if appeared_in_pnl else 0,
        1 if appeared_in_vol else 0,
        1 if min_volume_ok else 0,
        observed_at,
        observed_at,
    )


# -- smart_money_per_market ---------------------------------------------------

SMART_MONEY_PER_MARKET_COLS = (
    "condition_id",
    "yes_wallets",
    "no_wallets",
    "yes_size",
    "no_size",
    "yes_size_usdc",
    "no_size_usdc",
    "yes_avg_entry",
    "no_avg_entry",
    "net_size_usdc",
    "consensus_score",
    "sample_wallets",
    "dominant_outcome",
    "event_time",
    "observed_at",
)


def smart_money_per_market_row(
    *,
    condition_id: str,
    yes_wallets: int,
    no_wallets: int,
    yes_size: float,
    no_size: float,
    yes_size_usdc: float,
    no_size_usdc: float,
    yes_avg_entry: float | None,
    no_avg_entry: float | None,
    sample_wallets: int,
    observed_at: datetime,
) -> tuple[Any, ...]:
    net_usdc = float(yes_size_usdc) - float(no_size_usdc)
    total_usdc = float(yes_size_usdc) + float(no_size_usdc)
    consensus = 0.0 if total_usdc <= 0.0 else net_usdc / total_usdc
    if yes_wallets > 0 and no_wallets == 0:
        dominant = "YES"
    elif no_wallets > 0 and yes_wallets == 0:
        dominant = "NO"
    else:
        dominant = "MIXED"
    return (
        condition_id,
        int(yes_wallets),
        int(no_wallets),
        float(yes_size),
        float(no_size),
        float(yes_size_usdc),
        float(no_size_usdc),
        yes_avg_entry,
        no_avg_entry,
        net_usdc,
        float(consensus),
        int(sample_wallets),
        dominant,
        observed_at,
        observed_at,
    )


# -- signal_events ------------------------------------------------------------

SIGNAL_EVENTS_COLS = (
    "event_id",
    "event_type",
    "condition_id",
    "severity",
    "actor",
    "direction",
    "size_delta_usdc",
    "prior_size_usdc",
    "current_size_usdc",
    "payload",
    "event_time",
    "observed_at",
)


def signal_event_row(
    *,
    event_id: str,
    event_type: str,
    condition_id: str,
    severity: float,
    actor: str,
    direction: str,
    size_delta_usdc: float | None,
    prior_size_usdc: float | None,
    current_size_usdc: float | None,
    payload: dict[str, Any] | None,
    event_time: datetime,
    observed_at: datetime,
) -> tuple[Any, ...]:
    payload_json = json.dumps(payload, sort_keys=True) if payload else ""
    return (
        event_id,
        event_type,
        condition_id,
        float(severity),
        actor,
        direction,
        size_delta_usdc,
        prior_size_usdc,
        current_size_usdc,
        payload_json,
        event_time,
        observed_at,
    )


# -- external_events ---------------------------------------------------------

EXTERNAL_EVENTS_COLS = (
    "event_kind",
    "source",
    "source_uri",
    "source_id",
    "related_markets",
    "author",
    "title",
    "body",
    "url",
    "metadata",
    "payload_hash",
    "event_time",
    "observed_at",
)


def external_event_row(
    *,
    event_kind: str,
    source: str,
    source_uri: str,
    source_id: str,
    related_markets: list[str],
    author: str | None,
    title: str | None,
    body: str | None,
    url: str | None,
    metadata: dict[str, Any] | None,
    event_time: datetime,
    observed_at: datetime,
) -> tuple[Any, ...]:
    metadata_json = json.dumps(metadata, sort_keys=True) if metadata else ""
    payload_hash = hashlib.sha256(
        json.dumps(
            {
                "event_kind": event_kind,
                "source": source,
                "source_id": source_id,
                "source_uri": source_uri,
                "title": title or "",
                "body": body or "",
                "url": url or "",
                "metadata": metadata or {},
                "event_time": event_time.isoformat(),
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return (
        event_kind,
        source,
        source_uri,
        source_id,
        list(related_markets),
        author or "",
        title or "",
        body or "",
        url or "",
        metadata_json,
        payload_hash,
        event_time,
        observed_at,
    )


# -- market_features ----------------------------------------------------------

MARKET_FEATURES_COLS = (
    "condition_id",
    "token_id",
    "mid",
    "spread",
    "book_imbalance_1pct",
    "book_imbalance_5pct",
    "momentum_1h",
    "momentum_24h",
    "momentum_7d",
    "realized_vol_24h",
    "informed_taker_flow_24h",
    "passive_maker_flow_24h",
    "decayed_directional_flow_24h",
    "event_time",
    "observed_at",
)


def market_features_row(
    *,
    condition_id: str,
    token_id: str,
    mid: float,
    spread: float,
    book_imbalance_1pct: float | None,
    book_imbalance_5pct: float | None,
    momentum_1h: float | None,
    momentum_24h: float | None,
    momentum_7d: float | None,
    realized_vol_24h: float | None,
    informed_taker_flow_24h: float | None,
    passive_maker_flow_24h: float | None,
    decayed_directional_flow_24h: float | None,
    observed_at: datetime,
) -> tuple[Any, ...]:
    return (
        condition_id,
        token_id,
        float(mid),
        float(spread),
        book_imbalance_1pct,
        book_imbalance_5pct,
        momentum_1h,
        momentum_24h,
        momentum_7d,
        realized_vol_24h,
        informed_taker_flow_24h,
        passive_maker_flow_24h,
        decayed_directional_flow_24h,
        observed_at,
        observed_at,
    )


# -- market_classifications --------------------------------------------------

CLASSIFICATIONS_COLS = (
    "condition_id",
    "market_type",
    "confidence",
    "needs_review",
    "reviewer_flag",
    "classifier",
    "asset",
    "strike",
    "range_low",
    "range_high",
    "direction",
    "event_family",
    "resolution_date",
    "reasons",
    "event_time",
    "observed_at",
)


def classification_row(
    *,
    condition_id: str,
    market_type: str,
    confidence: float,
    needs_review: bool,
    asset: str | None,
    strike: float | None,
    range_low: float | None,
    range_high: float | None,
    direction: str | None,
    event_family: str | None,
    resolution_date: datetime | None,
    reasons: list[str],
    observed_at: datetime,
    classifier: str = "regex_v1",
    reviewer_flag: str = "",
) -> tuple[Any, ...]:
    """Shape a :class:`model.ClassificationResult` into a ``market_classifications`` row.

    Low-confidence / MISC rows get ``reviewer_flag='pending'`` so the review
    queue UI can surface them; approved / rejected flows land in M1.1.
    """
    flag = reviewer_flag or ("pending" if needs_review else "")
    return (
        condition_id,
        market_type,
        float(confidence),
        1 if needs_review else 0,
        flag,
        classifier,
        asset or "",
        strike,
        range_low,
        range_high,
        direction or "",
        event_family or "",
        resolution_date,
        "|".join(reasons),
        observed_at,
        observed_at,
    )


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


# -- market_resolution_risk --------------------------------------------------

RESOLUTION_RISK_COLS = (
    "condition_id",
    "risk_score",
    "risk_level",
    "is_flagged",
    "risk_multiplier",
    "classifier",
    "reasons",
    "event_time",
    "observed_at",
)


def resolution_risk_row(
    *,
    condition_id: str,
    risk_score: float,
    risk_level: str,
    is_flagged: bool,
    risk_multiplier: float,
    classifier: str,
    reasons: list[str],
    observed_at: datetime,
) -> tuple[Any, ...]:
    return (
        condition_id,
        float(risk_score),
        risk_level,
        1 if is_flagged else 0,
        float(risk_multiplier),
        classifier,
        "|".join(reasons),
        observed_at,
        observed_at,
    )


# -- deribit_iv_surface ------------------------------------------------------

DERIBIT_IV_SURFACE_COLS = (
    "currency",
    "expiry_date",
    "expiry_days",
    "underlying_price",
    "atm_iv",
    "call_otm_iv",
    "put_otm_iv",
    "strike_skew",
    "atm_instrument",
    "call_otm_instrument",
    "put_otm_instrument",
    "event_time",
    "observed_at",
)


def deribit_iv_surface_row(
    *,
    currency: str,
    expiry_date: datetime,
    expiry_days: float,
    underlying_price: float,
    atm_iv: float,
    call_otm_iv: float | None,
    put_otm_iv: float | None,
    strike_skew: float | None,
    atm_instrument: str,
    call_otm_instrument: str | None,
    put_otm_instrument: str | None,
    observed_at: datetime,
) -> tuple[Any, ...]:
    return (
        currency,
        expiry_date.date(),
        float(expiry_days),
        float(underlying_price),
        float(atm_iv),
        float(call_otm_iv) if call_otm_iv is not None else None,
        float(put_otm_iv) if put_otm_iv is not None else None,
        float(strike_skew) if strike_skew is not None else None,
        atm_instrument,
        call_otm_instrument or "",
        put_otm_instrument or "",
        observed_at,
        observed_at,
    )


# -- validated_spot_prices ---------------------------------------------------

VALIDATED_SPOT_PRICE_COLS = (
    "asset",
    "reference_price",
    "binance_price",
    "coinbase_price",
    "coingecko_price",
    "accepted_venues",
    "rejected_venues",
    "max_divergence_bps",
    "is_valid",
    "event_time",
    "observed_at",
)


def validated_spot_price_row(
    *,
    asset: str,
    reference_price: float | None,
    quotes: list[SpotTickerSnapshot],
    accepted_venues: list[str],
    rejected_venues: list[str],
    max_divergence_bps: float,
    is_valid: bool,
    observed_at: datetime,
) -> tuple[Any, ...]:
    by_venue = {quote.venue: quote for quote in quotes}
    return (
        asset,
        float(reference_price) if reference_price is not None else None,
        float(by_venue["binance"].price) if "binance" in by_venue else None,
        float(by_venue["coinbase"].price) if "coinbase" in by_venue else None,
        float(by_venue["coingecko"].price) if "coingecko" in by_venue else None,
        list(accepted_venues),
        list(rejected_venues),
        float(max_divergence_bps),
        1 if is_valid else 0,
        observed_at,
        observed_at,
    )


# -- perp_funding_basis ------------------------------------------------------

PERP_FUNDING_BASIS_COLS = (
    "venue",
    "asset",
    "symbol",
    "index_price",
    "mark_price",
    "basis_bps",
    "funding_rate",
    "funding_rate_annualized",
    "event_time",
    "observed_at",
)


MACRO_SERIES_COLS = (
    "source",
    "series_id",
    "series_title",
    "period_date",
    "value",
    "units",
    "event_time",
    "observed_at",
)


def macro_series_row(
    *,
    source: str,
    series_id: str,
    series_title: str,
    period_date: datetime,
    value: float | None,
    units: str,
    observed_at: datetime,
) -> tuple[Any, ...]:
    """Shape one macro observation into a ``macro_series`` row.

    ``period_date`` is the date the observation describes (e.g. CPI-for-March
    has period_date=2026-03-01). ``observed_at`` is *when we ingested* —
    crucial because FRED revises older observations weeks later, so the
    backtest needs to see only what was known at training-time.
    """
    return (
        source,
        series_id,
        series_title,
        period_date.date(),
        float(value) if value is not None else None,
        units,
        period_date,
        observed_at,
    )


ONCHAIN_METRICS_COLS = (
    "source",
    "metric_id",
    "metric_title",
    "asset",
    "chain",
    "category",
    "period_date",
    "value",
    "units",
    "event_time",
    "observed_at",
)


def onchain_metric_row(
    *,
    source: str,
    metric_id: str,
    metric_title: str,
    asset: str,
    chain: str,
    category: str,
    period_date: datetime,
    value: float | None,
    units: str,
    observed_at: datetime,
) -> tuple[Any, ...]:
    """Shape one on-chain observation into an ``onchain_metrics`` row.

    ``period_date`` is the date the observation describes (e.g. an exchange
    inflow window labelled 2026-04-23). ``observed_at`` is when we ingested —
    Glassnode/Dune both restate older observations after final settlement, so
    keeping both timestamps preserves the PIT contract.
    """
    return (
        source,
        metric_id,
        metric_title,
        asset,
        chain,
        category,
        period_date.date(),
        float(value) if value is not None else None,
        units,
        period_date,
        observed_at,
    )


MODEL_DRIFT_METRICS_COLS = (
    "window_label",
    "market_type",
    "ttr_bucket",
    "regime",
    "sample_count",
    "brier",
    "ece",
    "coverage",
    "avg_predicted",
    "avg_outcome",
    "baseline_brier",
    "brier_skill",
    "event_time",
    "observed_at",
)


def model_drift_metric_row(
    *,
    window_label: str,
    market_type: str,
    ttr_bucket: str,
    regime: str | None,
    sample_count: int,
    brier: float,
    ece: float,
    coverage: float | None,
    avg_predicted: float,
    avg_outcome: float,
    baseline_brier: float | None,
    brier_skill: float | None,
    observed_at: datetime,
) -> tuple[Any, ...]:
    return (
        window_label,
        market_type,
        ttr_bucket,
        regime or "",
        int(sample_count),
        float(brier),
        float(ece),
        float(coverage) if coverage is not None else None,
        float(avg_predicted),
        float(avg_outcome),
        float(baseline_brier) if baseline_brier is not None else None,
        float(brier_skill) if brier_skill is not None else None,
        observed_at,
        observed_at,
    )


MODEL_DISABLE_LOG_COLS = (
    "market_type",
    "action",
    "consecutive_days",
    "threshold_days",
    "reason",
    "event_time",
    "observed_at",
)


def model_disable_log_row(
    *,
    market_type: str,
    action: str,
    consecutive_days: int,
    threshold_days: int,
    reason: str,
    observed_at: datetime,
) -> tuple[Any, ...]:
    if action not in {"disabled", "re_enabled"}:
        raise ValueError(f"action must be 'disabled' or 're_enabled', got {action!r}")
    return (
        market_type,
        action,
        int(consecutive_days),
        int(threshold_days),
        reason,
        observed_at,
        observed_at,
    )


FEATURE_DRIFT_METRICS_COLS = (
    "feature_name",
    "reference_count",
    "current_count",
    "psi",
    "kl_divergence",
    "psi_threshold",
    "is_alert",
    "event_time",
    "observed_at",
)


def feature_drift_metric_row(
    *,
    feature_name: str,
    reference_count: int,
    current_count: int,
    psi: float,
    kl_divergence: float,
    psi_threshold: float,
    is_alert: bool,
    observed_at: datetime,
) -> tuple[Any, ...]:
    return (
        feature_name,
        int(reference_count),
        int(current_count),
        float(psi),
        float(kl_divergence),
        float(psi_threshold),
        bool(is_alert),
        observed_at,
        observed_at,
    )


SIGNAL_ABLATION_METRICS_COLS = (
    "signal_family",
    "sample_count",
    "signal_present_samples",
    "non_zero_contribution_samples",
    "mean_abs_probability_delta",
    "brier_with_signal",
    "brier_without_signal",
    "incremental_brier_gain",
    "bootstrap_ci_lo",
    "bootstrap_ci_hi",
    "ci_level",
    "bootstrap_iterations",
    "archive_recommended",
    "model_version",
    "horizon_hours",
    "resolved_lookback_days",
    "event_time",
    "observed_at",
)


def signal_ablation_metric_row(
    *,
    signal_family: str,
    sample_count: int,
    signal_present_samples: int,
    non_zero_contribution_samples: int,
    mean_abs_probability_delta: float | None,
    brier_with_signal: float | None,
    brier_without_signal: float | None,
    incremental_brier_gain: float | None,
    bootstrap_ci_lo: float | None,
    bootstrap_ci_hi: float | None,
    ci_level: float,
    bootstrap_iterations: int,
    archive_recommended: bool,
    model_version: str,
    horizon_hours: int,
    resolved_lookback_days: int,
    observed_at: datetime,
) -> tuple[Any, ...]:
    return (
        signal_family,
        int(sample_count),
        int(signal_present_samples),
        int(non_zero_contribution_samples),
        float(mean_abs_probability_delta) if mean_abs_probability_delta is not None else None,
        float(brier_with_signal) if brier_with_signal is not None else None,
        float(brier_without_signal) if brier_without_signal is not None else None,
        float(incremental_brier_gain) if incremental_brier_gain is not None else None,
        float(bootstrap_ci_lo) if bootstrap_ci_lo is not None else None,
        float(bootstrap_ci_hi) if bootstrap_ci_hi is not None else None,
        float(ci_level),
        int(bootstrap_iterations),
        bool(archive_recommended),
        model_version,
        int(horizon_hours),
        int(resolved_lookback_days),
        observed_at,
        observed_at,
    )


SIGNAL_ARCHIVE_LOG_COLS = (
    "signal_family",
    "action",
    "sample_count",
    "incremental_brier_gain",
    "bootstrap_ci_lo",
    "bootstrap_ci_hi",
    "ci_level",
    "reason",
    "event_time",
    "observed_at",
)


def signal_archive_log_row(
    *,
    signal_family: str,
    action: str,
    sample_count: int,
    incremental_brier_gain: float | None,
    bootstrap_ci_lo: float | None,
    bootstrap_ci_hi: float | None,
    ci_level: float,
    reason: str,
    observed_at: datetime,
) -> tuple[Any, ...]:
    if action not in {"archived", "re_enabled"}:
        raise ValueError(f"unsupported signal archive action: {action!r}")
    return (
        signal_family,
        action,
        int(sample_count),
        float(incremental_brier_gain) if incremental_brier_gain is not None else None,
        float(bootstrap_ci_lo) if bootstrap_ci_lo is not None else None,
        float(bootstrap_ci_hi) if bootstrap_ci_hi is not None else None,
        float(ci_level),
        reason,
        observed_at,
        observed_at,
    )


RESOLUTION_RISK_CORPUS_COLS = (
    "condition_id",
    "question",
    "description",
    "snapshot_resolution_source",
    "first_resolved_outcome",
    "final_resolved_outcome",
    "final_resolution_source",
    "resolution_count",
    "revision_count",
    "invalid_count",
    "manual_count",
    "uma_count",
    "had_dispute",
    "unexpected_resolution",
    "dispute_statuses",
    "unexpected_reasons",
    "risk_score",
    "risk_level",
    "risk_flagged",
    "risk_classifier",
    "risk_reasons",
    "first_resolution_event_time",
    "first_resolution_observed_at",
    "event_time",
    "observed_at",
)


def resolution_risk_corpus_row(
    *,
    condition_id: str,
    question: str,
    description: str,
    snapshot_resolution_source: str,
    first_resolved_outcome: str,
    final_resolved_outcome: str,
    final_resolution_source: str,
    resolution_count: int,
    revision_count: int,
    invalid_count: int,
    manual_count: int,
    uma_count: int,
    had_dispute: bool,
    unexpected_resolution: bool,
    dispute_statuses: list[str],
    unexpected_reasons: list[str],
    risk_score: float | None,
    risk_level: str,
    risk_flagged: bool,
    risk_classifier: str,
    risk_reasons: list[str],
    first_resolution_event_time: datetime,
    first_resolution_observed_at: datetime,
    observed_at: datetime,
) -> tuple[Any, ...]:
    return (
        condition_id,
        question,
        description,
        snapshot_resolution_source,
        first_resolved_outcome,
        final_resolved_outcome,
        final_resolution_source,
        int(resolution_count),
        int(revision_count),
        int(invalid_count),
        int(manual_count),
        int(uma_count),
        bool(had_dispute),
        bool(unexpected_resolution),
        "|".join(dispute_statuses),
        "|".join(unexpected_reasons),
        float(risk_score) if risk_score is not None else None,
        risk_level,
        bool(risk_flagged),
        risk_classifier,
        "|".join(risk_reasons),
        first_resolution_event_time,
        first_resolution_observed_at,
        observed_at,
        observed_at,
    )


REGIME_LABELS_COLS = (
    "regime_date",
    "label",
    "confidence",
    "btc_realized_vol_24h",
    "btc_realized_vol_7d",
    "btc_momentum_7d",
    "btc_ndx_correlation_30d",
    "stablecoin_supply_delta_7d",
    "reasons",
    "classifier",
    "event_time",
    "observed_at",
)


def regime_label_row(
    *,
    regime_date: datetime,
    label: str,
    confidence: float,
    btc_realized_vol_24h: float | None,
    btc_realized_vol_7d: float | None,
    btc_momentum_7d: float | None,
    btc_ndx_correlation_30d: float | None,
    stablecoin_supply_delta_7d: float | None,
    reasons: list[str],
    classifier: str,
    observed_at: datetime,
) -> tuple[Any, ...]:
    """Shape one regime classification into a ``regime_labels`` row.

    ``regime_date`` is the calendar UTC date the label applies to;
    ``event_time`` is its midnight UTC and ``observed_at`` is when we tagged
    it. PIT replays compare ``observed_at`` to ``asked_at`` so the model only
    sees the regime that was live at prediction time.
    """
    midnight = datetime(regime_date.year, regime_date.month, regime_date.day, tzinfo=UTC)
    return (
        regime_date.date() if hasattr(regime_date, "date") else regime_date,
        label,
        float(confidence),
        btc_realized_vol_24h,
        btc_realized_vol_7d,
        btc_momentum_7d,
        btc_ndx_correlation_30d,
        stablecoin_supply_delta_7d,
        "|".join(reasons),
        classifier,
        midnight,
        observed_at,
    )


def perp_funding_basis_row(
    *,
    venue: str,
    asset: str,
    symbol: str,
    index_price: float,
    mark_price: float,
    funding_rate: float | None,
    funding_cycles_per_day: float = 3.0,
    observed_at: datetime,
) -> tuple[Any, ...]:
    """Shape one perp snapshot into a ``perp_funding_basis`` row.

    The Binance perp endpoint emits ``lastFundingRate`` as the rate for the
    most recently settled 8h window. We carry the raw rate AND a derived
    annualized figure (``funding_cycles_per_day x 365``) so dashboards can
    compare across venues without re-applying the multiplier.
    """
    basis_bps = (
        0.0
        if index_price <= 0
        else (mark_price - index_price) / index_price * 10_000.0
    )
    annualized = (
        float(funding_rate) * funding_cycles_per_day * 365.0
        if funding_rate is not None
        else None
    )
    return (
        venue,
        asset,
        symbol,
        float(index_price),
        float(mark_price),
        float(basis_bps),
        float(funding_rate) if funding_rate is not None else None,
        annualized,
        observed_at,
        observed_at,
    )
