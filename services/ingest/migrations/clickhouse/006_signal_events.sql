-- M3.3 / M3.5: unified signal-event log (PRD §4 "Signal Feed", §6).
--
-- Append-only event stream consumed by the UI signal-feed strip and by
-- alert/notification plumbing. All signal producers write here:
--
--   * whale_open   — qualified smart-money wallet takes a new position above
--                    the PRD §3.3 threshold (10k USDC or 20% of wallet size)
--   * whale_resize — qualified wallet grows/trims an existing position above
--                    the same threshold
--   * whale_close  — qualified wallet exits
--   * arb          — no-arb violation detected across sibling markets (§3.4)
--   * large_print  — single trade prints above a per-market threshold (§3.5)
--   * book_shock   — book imbalance shock (§3.5)
--
-- PIT contract: ``observed_at`` is the wall-clock "when we learned" key;
-- ``event_time`` is when the underlying event happened (usually equal to
-- observed_at for derived signals). We never UPDATE — corrections are new
-- rows with the same ``event_id`` + a later observed_at.

USE polypredictor;

CREATE TABLE IF NOT EXISTS signal_events (
    event_id            String,
    event_type          LowCardinality(String),
    condition_id        String,
    severity            Float64,                -- normalized magnitude ∈ [0, ~∞)
    actor               String,                 -- wallet / source id; '' if unused
    direction           LowCardinality(String), -- 'yes' | 'no' | 'neutral'
    size_delta_usdc     Nullable(Float64),
    prior_size_usdc     Nullable(Float64),
    current_size_usdc   Nullable(Float64),
    payload             String,                 -- JSON blob for type-specific extras
    event_time          DateTime64(3, 'UTC'),
    observed_at         DateTime64(3, 'UTC')
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(observed_at)
ORDER BY (observed_at, event_type, event_id)
TTL toDate(observed_at) + INTERVAL 2 YEAR;

CREATE INDEX IF NOT EXISTS signal_events_condition_idx
    ON signal_events (condition_id)
    TYPE bloom_filter GRANULARITY 4;
