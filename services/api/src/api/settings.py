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
    cors_allow_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    auth_session_secret_b64: str | None = None
    auth_session_cookie_name: str = "polypredictor_session"
    auth_session_ttl_seconds: int = 60 * 60 * 24 * 30
    auth_session_cookie_secure: bool = False
    # When True, /v1/auth/request-magic-link returns the token in the
    # response body. Should ONLY be true in local development. Staging /
    # production must keep this false; tokens leave via email (or, while
    # the mailer is being wired, via structured logs the operator owns).
    auth_magic_link_in_band: bool = False
    # Master switch for per-route auth enforcement on user-scoped routes.
    # When False, ungated routes still resolve to the demo user so the
    # dashboard keeps working before the magic-link login UI ships. Flip to
    # True once the frontend can drive a session.
    auth_enforce_user_routes: bool = False
    admin_emails: str = ""
    error_reports_max_body_bytes: int = 64 * 1024
    error_reports_rate_limit_per_minute: int = 30
    # Drop a repeated error report within this window if its stack hash
    # matches one already seen — keeps a single noisy bug from filling the
    # `error_reports` table.
    error_reports_dedupe_window_seconds: int = 3600

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
    # X/Twitter Basic API (M4.1 decision: paid API chosen).
    x_bearer_token: str | None = None
    x_filtered_stream_rules_file: str | None = None
    # Stripe billing (v1 launch).
    stripe_secret_key: str | None = None
    stripe_price_id: str | None = None
    stripe_webhook_secret: str | None = None
    billing_monthly_price_usd: float = 49.0
    billing_success_url: str = "http://localhost:3000/billing/success"
    billing_cancel_url: str = "http://localhost:3000/pricing"
    # Waitlist.
    waitlist_limit: int = 100
    # P0/P1 source SLA config (M5 audit).
    p0_p1_sources_file: str | None = None
    source_failure_audit_window_hours: int = 24
    source_failure_audit_threshold: float = 0.01
    # EOL monitor (M8.3).
    eol_monitor_lookback_days: int = 30
    # Time-to-decision (M8.5).
    decision_time_window_hours: int = 48
    decision_time_target_minutes: float = 5.0
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
    # PRD §9 cost watch — per-1000-request operator-configured prices for
    # paid third-party APIs. Default 0 keeps free sources at $0; operators
    # override via env (`COST_PER_1K_REQUESTS_X_API`, etc.) once a contract
    # is signed. The cost-watch endpoint joins these with `ingest_health`
    # 24h request counts to surface running spend per provider.
    cost_per_1k_requests_x_api: float = 0.0
    cost_per_1k_requests_glassnode: float = 0.0
    cost_per_1k_requests_dune: float = 0.0
    cost_per_1k_requests_cme_fedwatch: float = 0.0
    cost_per_1k_requests_deribit: float = 0.0
    cost_per_1k_requests_polymarket: float = 0.0
    cost_watch_lookback_hours: int = 24
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
    # Continuous market distribution output (Open Q decision).
    distribution_n_points: int = 25
    # Boot-time Postgres migration runner. Default path works in docker
    # (services/ingest is copied into /app) and locally (relative to repo).
    postgres_migrations_dir: str = "services/ingest/migrations/postgres"
    postgres_migrations_apply_on_startup: bool = True
    # Boot-time ClickHouse migration runner. Same volume-staleness problem
    # as Postgres: clickhouse-server only runs /docker-entrypoint-initdb.d
    # scripts on first init, so the api closes the gap defensively.
    clickhouse_migrations_dir: str = "services/ingest/migrations/clickhouse"
    clickhouse_migrations_apply_on_startup: bool = True

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
