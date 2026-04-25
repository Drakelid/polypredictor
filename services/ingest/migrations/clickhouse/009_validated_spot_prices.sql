-- M5: cross-validated spot prices across Binance / Coinbase / CoinGecko.
--
-- One row per asset per polling instant. Divergent venue samples are retained
-- for auditability but excluded from the consensus reference_price.

USE polypredictor;

CREATE TABLE IF NOT EXISTS validated_spot_prices (
    asset                   LowCardinality(String),
    reference_price         Nullable(Float64),
    binance_price           Nullable(Float64),
    coinbase_price          Nullable(Float64),
    coingecko_price         Nullable(Float64),
    accepted_venues         Array(String),
    rejected_venues         Array(String),
    max_divergence_bps      Float64,
    is_valid                UInt8,
    event_time              DateTime64(3, 'UTC'),
    observed_at             DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(observed_at)
PARTITION BY toYYYYMM(observed_at)
ORDER BY (asset, observed_at)
TTL toDate(observed_at) + INTERVAL 2 YEAR;
