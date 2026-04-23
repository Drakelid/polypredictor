"""Runtime settings loaded from env. Pydantic-settings handles .env discovery."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: str = "development"
    log_level: str = "INFO"

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "polypredictor"
    postgres_user: str = "polypredictor"
    postgres_password: str = "polypredictor_dev"

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
    cme_fedwatch_base: str = "https://markets.api.cmegroup.com/fedwatch/v1"
    cme_oauth_token_url: str = "https://auth.cmegroup.com/as/token.oauth2"
    cme_api_id: str | None = None
    cme_api_secret: str | None = None
    cme_application_name: str = "PolyPredictor"
    cme_application_vendor: str = "PolyPredictor"
    cme_application_version: str = "0.0.1"
    discrete_baselines_file: str | None = "services/api/data/discrete_baselines.json"
    ensemble_registry_file: str | None = None
    conformal_registry_file: str | None = None
    resolution_risk_multiplier_default: float = 1.0
    fractional_kelly_default: float = 0.25
    kelly_cap_default: float = 0.05
    journal_demo_user_email: str = "demo@polypredictor.local"
    journal_demo_user_name: str = "Demo User"
    journal_default_size_usdc: float = 100.0
    # Legacy fallback when no manual/cached JSON file is present.
    fomc_fedwatch_prob: float | None = None
    cpi_consensus_point: float | None = None
    cpi_consensus_stddev: float | None = None
    nfp_consensus_point: float | None = None
    nfp_consensus_stddev: float | None = None

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
