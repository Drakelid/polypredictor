-- M5: macro time-series observations (FRED / BLS).
--
-- One row per (source, series_id, period_date, observed_at). Append-only —
-- when an observation is revised (FRED publishes restatements), the
-- restatement is a new row at a later observed_at, never an UPDATE. Backtests
-- read with ``observed_at <= asked_at`` so the model only ever sees data that
-- was actually published before the asked-at moment.

USE polypredictor;

CREATE TABLE IF NOT EXISTS macro_series (
    source            LowCardinality(String),   -- 'fred' | 'bls'
    series_id         String,                    -- e.g. 'CPIAUCSL', 'UNRATE', 'FEDFUNDS'
    series_title      String,
    period_date       Date,                      -- the date the observation is FOR
    value             Nullable(Float64),
    units             String,
    event_time        DateTime64(3, 'UTC'),     -- when the data point applies (== period_date)
    observed_at       DateTime64(3, 'UTC')      -- when we ingested it
)
ENGINE = ReplacingMergeTree(observed_at)
PARTITION BY toYYYYMM(observed_at)
ORDER BY (source, series_id, period_date, observed_at)
TTL toDate(observed_at) + INTERVAL 5 YEAR;
