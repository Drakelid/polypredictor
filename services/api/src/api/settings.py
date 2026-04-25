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
    user_secret_encryption_key_b64: str | None = None
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
    drift_monitor_horizon_hours: int = 24
    drift_monitor_limit: int = 1000
    drift_monitor_resolved_lookback_days: int = 120
    drift_monitor_disable_streak_days: int = 7
    drift_monitor_history_lookback_days: int = 60
    feature_drift_current_window_days: int = 7
    feature_drift_reference_window_days: int = 90
    feature_drift_alert_threshold_psi: float = 0.2
    feature_drift_min_samples: int = 20
    signal_ablation_horizon_hours: int = 24
    signal_ablation_limit: int = 1000
    signal_ablation_resolved_lookback_days: int = 180
    signal_ablation_bootstrap_iterations: int = 500
    signal_ablation_ci_level: float = 0.95
    signal_ablation_min_samples: int = 50
    status_source_lookback_hours: int = 24
    status_source_failure_rate_threshold: float = 0.01
    status_source_staleness_minutes: int = 30
    beta_invite_target_count: int = 20
    resolution_risk_corpus_limit: int = 5000
    resolution_risk_corpus_lookback_days: int = 3650
    resolution_risk_multiplier_default: float = 1.0
    adversarial_flow_min_flow_strength: float = 0.15
    adversarial_flow_signal_window_hours: int = 24
    adversarial_flow_external_event_window_hours: int = 24
    adversarial_flow_flag_threshold: float = 0.65
    adversarial_flow_blend_floor: float = 0.25
    thin_book_top_level_depth_threshold_usdc: float = 1000.0
    fractional_kelly_default: float = 0.25
    kelly_cap_default: float = 0.05
    external_decay_default_half_life_minutes: float = 360.0
    external_decay_reddit_half_life_minutes: float = 180.0
    external_decay_rss_half_life_minutes: float = 360.0
    external_decay_x_half_life_minutes: float = 120.0
    external_decay_discord_half_life_minutes: float = 120.0
    external_decay_macro_half_life_minutes: float = 1_440.0
    external_decay_onchain_half_life_minutes: float = 720.0
    # PRD §6: down-weight smart_money_consensus when holder concentration
    # (max of YES/NO Gini) exceeds this threshold. Linear attenuation toward
    # zero as concentration approaches 1.
    holder_concentration_down_weight_threshold: float = 0.6
    journal_demo_user_email: str = "demo@polypredictor.local"
    journal_demo_user_name: str = "Demo User"
    journal_default_size_usdc: float = 100.0
    dp_aggregate_epsilon: float = 1.0
    dp_aggregate_min_users: int = 5
    dp_aggregate_min_calls: int = 20
    dp_aggregate_lookback_days: int = 365
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
