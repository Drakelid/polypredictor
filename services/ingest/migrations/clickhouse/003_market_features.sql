USE polypredictor;

-- Feature snapshots for the M2 market-microstructure pipeline ---------------
-- One append-only row per market feature snapshot. These are derived from the
-- PIT-safe raw stores (quotes, orderbooks, trades) and are themselves queried
-- with observed_at <= asked_at during training/backtests.

CREATE TABLE IF NOT EXISTS market_features (
    condition_id                 String,
    token_id                     String,
    mid                          Float64,
    spread                       Float64,
    book_imbalance_1pct          Nullable(Float64),
    book_imbalance_5pct          Nullable(Float64),
    momentum_1h                  Nullable(Float64),
    momentum_24h                 Nullable(Float64),
    momentum_7d                  Nullable(Float64),
    realized_vol_24h             Nullable(Float64),
    informed_taker_flow_24h      Nullable(Float64),
    passive_maker_flow_24h       Nullable(Float64),
    decayed_directional_flow_24h Nullable(Float64),
    event_time                   DateTime64(3, 'UTC'),
    observed_at                  DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(observed_at)
PARTITION BY toYYYYMM(observed_at)
ORDER BY (condition_id, observed_at, token_id)
TTL toDate(observed_at) + INTERVAL 2 YEAR;
