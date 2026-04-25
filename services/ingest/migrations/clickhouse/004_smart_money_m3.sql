-- M3.1: smart-money qualification + per-market aggregation (PRD §6 / §9).
--
-- Two new tables:
--
--   * ``smart_money_qualifications`` — append-only history of which leaderboard
--     wallets pass the wash-trading guards (PnL+VOL cross-check, multi-market
--     requirement, minimum volume). One row per wallet per refresh; the
--     ``qualified=1`` subset is the rolling ~500-wallet smart-money cohort.
--
--   * ``smart_money_per_market`` — per-market rollup of positions held by the
--     *qualified* cohort. Consumers (ensemble, signal feed) read the latest
--     row for each ``condition_id``; the 24h directional delta is derived at
--     read time from a prior observation.
--
-- PIT contract holds: every row carries observed_at and is queried with
-- observed_at <= asked_at so backtests reproduce the cohort that was live at
-- prediction time.

USE polypredictor;

CREATE TABLE IF NOT EXISTS smart_money_qualifications (
    proxy_wallet            String,
    qualified               UInt8,
    -- Reasons for the qualification decision (flags that failed, or 'qualified').
    reasons                 Array(String),
    distinct_markets        UInt32,
    leaderboard_rank        Nullable(UInt32),
    leaderboard_pnl         Nullable(Float64),
    leaderboard_vol         Nullable(Float64),
    sources                 Array(String),          -- 'CRYPTO:MONTH:PNL', ...
    appeared_in_pnl         UInt8,
    appeared_in_vol         UInt8,
    min_volume_ok           UInt8,
    event_time              DateTime64(3, 'UTC'),
    observed_at             DateTime64(3, 'UTC')
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(observed_at)
ORDER BY (proxy_wallet, observed_at)
TTL toDate(observed_at) + INTERVAL 2 YEAR;

CREATE TABLE IF NOT EXISTS smart_money_per_market (
    condition_id            String,
    yes_wallets             UInt32,
    no_wallets              UInt32,
    yes_size               Float64,
    no_size                Float64,
    yes_size_usdc           Float64,
    no_size_usdc            Float64,
    yes_avg_entry           Nullable(Float64),
    no_avg_entry            Nullable(Float64),
    net_size_usdc           Float64,                -- YES notional − NO notional
    -- Consensus score in [-1, 1]: (YES − NO) / (YES + NO) by USDC notional.
    consensus_score         Float64,
    sample_wallets          UInt32,                 -- unique wallets with any position
    dominant_outcome        Enum8('YES' = 1, 'NO' = 2, 'MIXED' = 3),
    event_time              DateTime64(3, 'UTC'),
    observed_at             DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(observed_at)
PARTITION BY toYYYYMM(observed_at)
ORDER BY (condition_id, observed_at)
TTL toDate(observed_at) + INTERVAL 2 YEAR;
