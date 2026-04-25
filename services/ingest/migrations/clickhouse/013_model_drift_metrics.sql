-- M6.3: nightly drift snapshots (PRD §6.3 / §8 auto-disable rule).
--
-- One row per (window_label, market_type, regime, ttr_bucket, observed_at).
-- Append-only: every nightly run writes a fresh batch so historical drift
-- can be plotted directly. The auto-disable driver reads the trailing N
-- rows for a given (window_label='30d', market_type) tuple and decides via
-- :func:`model.backtest.should_auto_disable`.
--
-- Auto-disable status itself goes in a separate ``model_disable_log`` so
-- the on-call dashboard can render the timeline of disable / re-enable
-- transitions without scanning the whole drift_metrics table.

USE polypredictor;

CREATE TABLE IF NOT EXISTS model_drift_metrics (
    window_label        LowCardinality(String),   -- '7d' | '30d' | '90d'
    market_type         LowCardinality(String),   -- threshold / range / ... or 'overall'
    ttr_bucket          LowCardinality(String),   -- 'lt_1d' / '1d_7d' / '7d_30d' / 'gt_30d' / 'overall'
    regime              LowCardinality(String),   -- '' when not stratified by regime
    sample_count        UInt32,
    brier               Float64,
    ece                 Float64,
    coverage            Nullable(Float64),
    avg_predicted       Float64,
    avg_outcome         Float64,
    baseline_brier      Nullable(Float64),        -- market-implied prior on the same samples
    brier_skill         Nullable(Float64),
    event_time          DateTime64(3, 'UTC'),
    observed_at         DateTime64(3, 'UTC')
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(observed_at)
ORDER BY (window_label, market_type, ttr_bucket, regime, observed_at)
TTL toDate(observed_at) + INTERVAL 2 YEAR;

CREATE TABLE IF NOT EXISTS model_disable_log (
    market_type         LowCardinality(String),
    action              Enum8('disabled' = 1, 're_enabled' = 2),
    consecutive_days    UInt16,
    threshold_days      UInt16,
    reason              String,
    event_time          DateTime64(3, 'UTC'),
    observed_at         DateTime64(3, 'UTC')
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(observed_at)
ORDER BY (market_type, observed_at)
TTL toDate(observed_at) + INTERVAL 5 YEAR;
