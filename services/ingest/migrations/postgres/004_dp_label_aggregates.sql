-- Differentially private cross-user aggregate snapshots (M7.4)

CREATE TABLE IF NOT EXISTS dp_label_aggregates (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cohort_kind         TEXT NOT NULL,
    bucket_label        TEXT NOT NULL,
    source_user_count   INTEGER NOT NULL,
    raw_call_count      INTEGER NOT NULL,
    noisy_call_count    NUMERIC(20, 6) NOT NULL,
    raw_hit_rate        NUMERIC(12, 6) NOT NULL,
    noisy_hit_rate      NUMERIC(12, 6) NOT NULL,
    epsilon             NUMERIC(12, 6) NOT NULL,
    min_users           INTEGER NOT NULL,
    min_calls           INTEGER NOT NULL,
    lookback_start      TIMESTAMPTZ NOT NULL,
    lookback_end        TIMESTAMPTZ NOT NULL,
    observed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS dp_label_aggregates_observed_idx
    ON dp_label_aggregates (observed_at DESC);
