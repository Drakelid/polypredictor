-- M1: market-type classifier output (PRD §6.1).
--
-- Keyed by (condition_id, observed_at). Re-classification (either because the
-- rules changed or because a human reviewer re-labelled it) produces a new
-- row — we never UPDATE. The API reads the latest row per condition_id with
-- observed_at <= asked_at so backtests reproduce the label that was live at
-- prediction time.

USE polypredictor;

CREATE TABLE IF NOT EXISTS market_classifications (
    condition_id        String,
    market_type         Enum8(
        'threshold' = 1,
        'range' = 2,
        'discrete_event' = 3,
        'multi_outcome' = 4,
        'long_tail_binary' = 5,
        'misc' = 6
    ),
    confidence          Float32,
    needs_review        UInt8,
    reviewer_flag       LowCardinality(String),   -- '' | 'pending' | 'approved' | 'rejected'
    classifier          LowCardinality(String),   -- 'regex_v1' | 'llm_v1' | 'human'
    -- Structured features extracted alongside the label. Optional; downstream
    -- baselines decide whether absence invalidates the prediction.
    asset               LowCardinality(String),
    strike              Nullable(Float64),
    range_low           Nullable(Float64),
    range_high          Nullable(Float64),
    direction           LowCardinality(String),   -- 'above' | 'below' | 'between' | ''
    event_family        LowCardinality(String),   -- 'fomc' | 'cpi' | 'nfp' | ...
    resolution_date     Nullable(DateTime64(3, 'UTC')),
    reasons             String,                    -- pipe-joined classifier reasons
    event_time          DateTime64(3, 'UTC'),
    observed_at         DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(observed_at)
PARTITION BY toYYYYMM(observed_at)
ORDER BY (condition_id, observed_at)
TTL toDate(observed_at) + INTERVAL 2 YEAR;
