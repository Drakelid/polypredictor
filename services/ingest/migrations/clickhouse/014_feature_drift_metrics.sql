-- M6.3: per-feature drift snapshots / alert hook (PRD §6.9).
--
-- One row per (feature_name, observed_at). The nightly drift-monitor driver
-- compares a recent live cross-section against a longer historical reference
-- window and records PSI / KL for each persisted feature. ``is_alert`` flips
-- when PSI breaches the configured threshold (default 0.2), giving the
-- on-call path an append-only alert timeline without mutating old rows.

USE polypredictor;

CREATE TABLE IF NOT EXISTS feature_drift_metrics (
    feature_name         LowCardinality(String),
    reference_count      UInt32,
    current_count        UInt32,
    psi                  Float64,
    kl_divergence        Float64,
    psi_threshold        Float64,
    is_alert             Bool,
    event_time           DateTime64(3, 'UTC'),
    observed_at          DateTime64(3, 'UTC')
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(observed_at)
ORDER BY (feature_name, observed_at)
TTL toDate(observed_at) + INTERVAL 2 YEAR;
