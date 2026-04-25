-- M6.3: monthly per-signal ablation snapshots / archive log (PRD §6.9).
--
-- ``signal_ablation_metrics`` stores one append-only snapshot per
-- (signal_family, observed_at) with the PIT replayed Brier delta between the
-- full served model and the same model with that signal family removed.
-- ``signal_archive_log`` tracks when a family is archived / re-enabled based
-- on whether the bootstrap CI of incremental Brier gain includes zero.

USE polypredictor;

CREATE TABLE IF NOT EXISTS signal_ablation_metrics (
    signal_family                  LowCardinality(String),
    sample_count                   UInt32,
    signal_present_samples         UInt32,
    non_zero_contribution_samples  UInt32,
    mean_abs_probability_delta     Nullable(Float64),
    brier_with_signal              Nullable(Float64),
    brier_without_signal           Nullable(Float64),
    incremental_brier_gain         Nullable(Float64),
    bootstrap_ci_lo                Nullable(Float64),
    bootstrap_ci_hi                Nullable(Float64),
    ci_level                       Float64,
    bootstrap_iterations           UInt32,
    archive_recommended            Bool,
    model_version                  LowCardinality(String),
    horizon_hours                  UInt16,
    resolved_lookback_days         UInt16,
    event_time                     DateTime64(3, 'UTC'),
    observed_at                    DateTime64(3, 'UTC')
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(observed_at)
ORDER BY (signal_family, observed_at)
TTL toDate(observed_at) + INTERVAL 2 YEAR;

CREATE TABLE IF NOT EXISTS signal_archive_log (
    signal_family          LowCardinality(String),
    action                 Enum8('archived' = 1, 're_enabled' = 2),
    sample_count           UInt32,
    incremental_brier_gain Nullable(Float64),
    bootstrap_ci_lo        Nullable(Float64),
    bootstrap_ci_hi        Nullable(Float64),
    ci_level               Float64,
    reason                 String,
    event_time             DateTime64(3, 'UTC'),
    observed_at            DateTime64(3, 'UTC')
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(observed_at)
ORDER BY (signal_family, observed_at)
TTL toDate(observed_at) + INTERVAL 5 YEAR;
