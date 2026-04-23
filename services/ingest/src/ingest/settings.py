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
    feature_snapshots_interval_s: float = 300.0
    feature_snapshots_batch_size: int = 200
    feature_snapshots_trade_decay_half_life_h: float = 6.0


@lru_cache(maxsize=1)
def get_settings() -> IngestSettings:
    return IngestSettings()
