-- M5: persisted Deribit IV / skew / term-structure snapshots.
--
-- One row per (currency, expiry_date, observed_at), append-only. This lets
-- future backtests reconstruct the options surface that was visible at a
-- given wall-clock time instead of recomputing from live Deribit data.

USE polypredictor;

CREATE TABLE IF NOT EXISTS deribit_iv_surface (
    currency                LowCardinality(String),
    expiry_date             Date,
    expiry_days             Float64,
    underlying_price        Float64,
    atm_iv                  Float64,
    call_otm_iv             Nullable(Float64),
    put_otm_iv              Nullable(Float64),
    strike_skew             Nullable(Float64),
    atm_instrument          String,
    call_otm_instrument     String,
    put_otm_instrument      String,
    event_time              DateTime64(3, 'UTC'),
    observed_at             DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(observed_at)
PARTITION BY toYYYYMM(observed_at)
ORDER BY (currency, expiry_date, observed_at)
TTL toDate(observed_at) + INTERVAL 2 YEAR;
