"""Settings for ingestion workers (mirrors the API's for now)."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class IngestSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: str = "development"
    log_level: str = "INFO"

    clickhouse_host: str = "localhost"
    clickhouse_port: int = 8123
    clickhouse_db: str = "polypredictor"
    clickhouse_user: str = "default"
    clickhouse_password: str = ""

    redis_url: str = "redis://localhost:6379/0"

    polymarket_gamma_base: str = "https://gamma-api.polymarket.com"
    polymarket_clob_base: str = "https://clob.polymarket.com"
    polymarket_data_base: str = "https://data-api.polymarket.com"
    polymarket_wss_base: str = "wss://ws-subscriptions-clob.polymarket.com"
    deribit_base: str = "https://www.deribit.com/api/v2"
    binance_base: str = "https://fapi.binance.com"
    coinbase_intx_base: str = "https://api.international.coinbase.com"
    binance_spot_base: str = "https://api.binance.com"
    coinbase_exchange_base: str = "https://api.exchange.coinbase.com"
    coingecko_base: str = "https://api.coingecko.com/api/v3"
    bls_public_api_base: str = "https://api.bls.gov/publicAPI/v2"
    deribit_iv_surface_interval_s: float = 300.0
    deribit_iv_surface_currencies: str = "BTC,ETH"
    perp_funding_basis_interval_s: float = 300.0
    perp_funding_basis_assets: str = "BTC,ETH,SOL"
    macro_series_interval_s: float = 86_400.0  # daily refresh
    macro_series_fred_config_file: str | None = (
        "services/ingest/data/fred_series.json"
    )
    macro_series_bls_config_file: str | None = (
        "services/ingest/data/bls_series.json"
    )
    macro_series_timeout_s: float = 30.0
    # M5 on-chain ingestion (Glassnode + Dune free tiers).
    # Both keys are optional. With neither key configured the worker no-ops
    # cleanly. Per-source config files describe which metrics to pull.
    onchain_metrics_interval_s: float = 86_400.0  # daily refresh
    onchain_metrics_timeout_s: float = 30.0
    glassnode_base: str = "https://api.glassnode.com/v1"
    glassnode_api_key: str | None = None
    onchain_metrics_glassnode_config_file: str | None = (
        "services/ingest/data/onchain_glassnode.json"
    )
    dune_base: str = "https://api.dune.com/api/v1"
    dune_api_key: str | None = None
    onchain_metrics_dune_config_file: str | None = (
        "services/ingest/data/onchain_dune.json"
    )
    # M6.2 daily regime tagger.
    regime_tagger_interval_s: float = 86_400.0
    regime_btc_symbol: str = "BTCUSDT"
    regime_lookback_days: int = 30
    spot_validation_interval_s: float = 300.0
    spot_validation_assets: str = "BTC,ETH,SOL"
    spot_validation_max_deviation_bps: float = 50.0
    spot_validation_min_venues: int = 2
    retention_years: int = 2
    retention_cold_archive_after_years: int = 1
    retention_cold_archive_uri: str | None = None

    # Ingest cadence
    discover_interval_s: float = 60.0
    clob_top_n: int = 20  # top-N markets polled every ~5s
    clob_top_interval_s: float = 5.0
    clob_tail_interval_s: float = 60.0
    prices_history_backfill_interval_s: float = 600.0
    prices_history_backfill_batch_size: int = 50
    prices_history_backfill_fidelity: int = 60
    trades_sweeper_interval_s: float = 30.0
    trades_sweeper_limit: int = 200
    trades_sweeper_skip_top_n: int = 20
    smart_money_refresh_interval_s: float = 86_400.0
    smart_money_leaderboard_limit: int = 500
    smart_money_positions_limit: int = 500
    # Wash-trading guards (PRD §9). The leaderboard does not expose a trade
    # count, so reported volume is used as the proxy that stands in for
    # "minimum trade count".
    smart_money_min_volume: float = 10_000.0
    smart_money_min_distinct_markets: int = 3
    # Whale-flow detector (PRD §3.3). Alert on an open/resize that breaches
    # either the absolute USDC floor or the per-wallet fractional move.
    whale_flow_open_threshold_usdc: float = 10_000.0
    whale_flow_resize_pct: float = 0.20
    arb_checker_interval_s: float = 60.0
    arb_checker_max_markets: int = 500
    arb_taker_fee_bps: float = 0.0
    external_divergence_interval_s: float = 300.0
    external_divergence_max_markets: int = 200
    external_divergence_min_gap: float = 0.10
    microstructure_signals_interval_s: float = 60.0
    microstructure_signals_max_markets: int = 200
    large_print_min_notional_usdc: float = 5_000.0
    book_shock_lookback_minutes: int = 15
    book_shock_min_delta: float = 0.40
    rss_interval_s: float = 300.0
    rss_feed_sources_file: str | None = "services/ingest/data/rss_feeds.json"
    rss_feed_timeout_s: float = 15.0
    rss_feed_max_markets: int = 500
    rss_feed_max_related_markets: int = 5
    reddit_interval_s: float = 900.0
    reddit_subreddits: str = "CryptoCurrency,wallstreetbets"
    reddit_fetch_limit: int = 50
    reddit_timeout_s: float = 15.0
    reddit_max_markets: int = 500
    reddit_max_related_markets: int = 5
    scheduled_events_interval_s: float = 3_600.0
    scheduled_events_file: str | None = "services/ingest/data/scheduled_events.json"
    scheduled_events_max_markets: int = 500
    scheduled_events_max_related_markets: int = 5
    feature_snapshots_interval_s: float = 300.0
    feature_snapshots_batch_size: int = 200
    feature_snapshots_trade_decay_half_life_h: float = 6.0
    # Holder-concentration poller (PRD §6 / §9).
    holders_poll_interval_s: float = 300.0  # matches /holders TTL (§7)
    holders_poll_batch_size: int = 20
    holders_poll_max_markets: int = 200
    holder_concentration_whale_threshold: float = 0.40  # single-wallet > 40% flags


@lru_cache(maxsize=1)
def get_settings() -> IngestSettings:
    return IngestSettings()
