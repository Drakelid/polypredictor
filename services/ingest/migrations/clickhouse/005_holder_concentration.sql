-- M3.2: per-market holder-concentration rollup (PRD §6 / §9).
--
-- ``holders_snapshots`` already captures the raw top-N holders per outcome
-- (migration 001). This table adds the derived concentration summary that
-- the dashboard, ensemble feature, and signal feed all consume. One row per
-- (condition_id, observed_at); ReplacingMergeTree so re-runs at the same
-- observed_at collapse.
--
-- PRD rules wired into the fields here:
--   * ``*_whale_flag=1`` iff any single wallet owns > 40% of that outcome.
--   * ``*_gini`` is the Gini coefficient on top-N holder sizes — high values
--     feed the "down-weight smart_money_consensus when concentration > 0.6"
--     rule from §6.

USE polypredictor;

CREATE TABLE IF NOT EXISTS market_concentration (
    condition_id            String,
    yes_gini                Nullable(Float64),
    no_gini                 Nullable(Float64),
    yes_top1_pct            Nullable(Float64),
    no_top1_pct             Nullable(Float64),
    yes_top5_pct            Nullable(Float64),
    no_top5_pct             Nullable(Float64),
    yes_whale_flag          UInt8,                  -- any single YES wallet > 40%
    no_whale_flag           UInt8,                  -- any single NO wallet > 40%
    any_whale_flag          UInt8,                  -- OR of the two above
    yes_holders_count       UInt32,
    no_holders_count        UInt32,
    max_gini                Nullable(Float64),      -- max(yes_gini, no_gini) for quick filters
    event_time              DateTime64(3, 'UTC'),
    observed_at             DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(observed_at)
PARTITION BY toYYYYMM(observed_at)
ORDER BY (condition_id, observed_at)
TTL toDate(observed_at) + INTERVAL 2 YEAR;
