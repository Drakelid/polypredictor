-- M5: on-chain time-series observations (Glassnode / Dune free tiers).
--
-- One row per (source, metric_id, asset, period_date, observed_at). Append-only
-- via ReplacingMergeTree(observed_at) so re-ingestion of the same window is
-- idempotent and a later observed_at wins on revisions. Backtests read with
-- ``observed_at <= asked_at`` so the model only sees what was published before
-- the asked-at moment.

USE polypredictor;

CREATE TABLE IF NOT EXISTS onchain_metrics (
    source            LowCardinality(String),   -- 'glassnode' | 'dune'
    metric_id         String,                    -- e.g. 'transactions/transfers_volume_to_exchanges_sum'
    metric_title      String,
    asset             LowCardinality(String),    -- 'BTC' | 'ETH' | 'USDC' | 'USDT' | '' for chain-wide
    chain             LowCardinality(String),    -- 'bitcoin' | 'ethereum' | 'multi' | ''
    category          LowCardinality(String),    -- 'flow' | 'supply' | 'whale' | 'misc'
    period_date       Date,
    value             Nullable(Float64),
    units             String,
    event_time        DateTime64(3, 'UTC'),
    observed_at       DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(observed_at)
PARTITION BY toYYYYMM(observed_at)
ORDER BY (source, metric_id, asset, period_date, observed_at)
TTL toDate(observed_at) + INTERVAL 5 YEAR;
