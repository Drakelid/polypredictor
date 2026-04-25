-- M6.2: daily regime labels (PRD §6.2 / §6.3).
--
-- One row per (regime_date, observed_at). Append-only — when the upstream
-- HMM (or the deterministic stand-in classifier) re-labels an older date
-- because the underlying daily series got revised, we write a new row at a
-- later observed_at instead of UPDATE-ing in place.
--
-- The conformal registry uses ``regime`` as a Mondrian axis and the
-- ensemble consumes it as a feature, so backtests need the PIT-correct
-- version of the label that was live at training/prediction time.

USE polypredictor;

CREATE TABLE IF NOT EXISTS regime_labels (
    regime_date                  Date,
    label                        LowCardinality(String),  -- bull_trend / bear_trend / chop / liquidity_crisis
    confidence                   Float32,
    btc_realized_vol_24h         Nullable(Float64),
    btc_realized_vol_7d          Nullable(Float64),
    btc_momentum_7d              Nullable(Float64),
    btc_ndx_correlation_30d      Nullable(Float64),
    stablecoin_supply_delta_7d   Nullable(Float64),
    reasons                      String,                    -- pipe-joined
    classifier                   LowCardinality(String),    -- 'rule_v1' | 'hmm_v1' eventually
    event_time                   DateTime64(3, 'UTC'),     -- == midnight UTC of regime_date
    observed_at                  DateTime64(3, 'UTC')      -- when we tagged it
)
ENGINE = ReplacingMergeTree(observed_at)
PARTITION BY toYYYYMM(observed_at)
ORDER BY (regime_date, observed_at)
TTL toDate(observed_at) + INTERVAL 5 YEAR;
