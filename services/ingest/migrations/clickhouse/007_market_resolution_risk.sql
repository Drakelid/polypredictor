-- M4.4 / F9: resolution-risk scores over market wording.
--
-- Append-only, PIT-safe derived labels over market resolution wording.
-- This starts as a heuristic classifier; a future UMA-trained model can write
-- new rows with a different classifier name.

USE polypredictor;

CREATE TABLE IF NOT EXISTS market_resolution_risk (
    condition_id        String,
    risk_score          Float32,
    risk_level          LowCardinality(String),   -- 'low' | 'medium' | 'high'
    is_flagged          UInt8,
    risk_multiplier     Float32,
    classifier          LowCardinality(String),   -- 'heuristic_v1' | future trained model ids
    reasons             String,                    -- pipe-joined reasons
    event_time          DateTime64(3, 'UTC'),
    observed_at         DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(observed_at)
PARTITION BY toYYYYMM(observed_at)
ORDER BY (condition_id, observed_at)
TTL toDate(observed_at) + INTERVAL 2 YEAR;
