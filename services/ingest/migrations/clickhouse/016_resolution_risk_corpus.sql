-- M4.4: resolution-risk training corpus.
--
-- This derived table snapshots the resolved-market corpus used to train and
-- evaluate the future UMA/dispute-aware resolution-risk classifier. It joins
-- the market text known at first resolution time with labels extracted from the
-- append-only ``market_resolutions`` history:
--
-- * ``had_dispute`` for explicit dispute / revision paths
-- * ``unexpected_resolution`` for invalid, revised, manual, or disputed paths
--
-- Rows are append-only by ``observed_at`` so later replay can recover the
-- exact corpus snapshot used for a given training run.

USE polypredictor;

CREATE TABLE IF NOT EXISTS resolution_risk_corpus (
    condition_id                   String,
    question                       String,
    description                    String,
    snapshot_resolution_source     String,
    first_resolved_outcome         LowCardinality(String),
    final_resolved_outcome         LowCardinality(String),
    final_resolution_source        LowCardinality(String),
    resolution_count               UInt16,
    revision_count                 UInt16,
    invalid_count                  UInt16,
    manual_count                   UInt16,
    uma_count                      UInt16,
    had_dispute                    Bool,
    unexpected_resolution          Bool,
    dispute_statuses               String,
    unexpected_reasons             String,
    risk_score                     Nullable(Float64),
    risk_level                     LowCardinality(String),
    risk_flagged                   Bool,
    risk_classifier                LowCardinality(String),
    risk_reasons                   String,
    first_resolution_event_time    DateTime64(3, 'UTC'),
    first_resolution_observed_at   DateTime64(3, 'UTC'),
    event_time                     DateTime64(3, 'UTC'),
    observed_at                    DateTime64(3, 'UTC')
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(observed_at)
ORDER BY (condition_id, observed_at)
TTL toDate(observed_at) + INTERVAL 2 YEAR;
