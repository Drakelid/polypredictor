-- Human-review queue for low-confidence market classifications (M1.1).
--
-- The deterministic classifier in ``packages/model/src/model/classifier.py``
-- is high-precision but flags low-confidence / MISC outputs via
-- ``ClassificationResult.needs_review``. Those rows land here so an
-- operator (or a downstream LLM-assisted reviewer) can pick a label.
--
-- Latest-first per condition_id: a fresh enqueue replaces the prior
-- pending row via the (condition_id, status) partial-unique index, so a
-- market never has two rows in 'pending' state at the same time.

CREATE TABLE IF NOT EXISTS market_classification_review (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    condition_id                TEXT NOT NULL,
    question                    TEXT NOT NULL,
    description                 TEXT,
    tags                        TEXT[] NOT NULL DEFAULT '{}',
    resolution_source           TEXT,
    end_date                    TIMESTAMPTZ,
    multi_outcome_sibling_count INTEGER NOT NULL DEFAULT 0,
    -- Preliminary outputs from the deterministic classifier.
    preliminary_market_type     TEXT NOT NULL,
    preliminary_confidence      NUMERIC(6, 4) NOT NULL,
    preliminary_reasons         TEXT[] NOT NULL DEFAULT '{}',
    -- Optional LLM-assisted refinement (operator-pluggable).
    llm_market_type             TEXT,
    llm_confidence              NUMERIC(6, 4),
    llm_rationale               TEXT,
    -- Workflow state.
    status                      TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'accepted', 'rejected', 'superseded')),
    reviewer_decision           TEXT,
    reviewer_notes              TEXT,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reviewed_at                 TIMESTAMPTZ
);

-- One pending row per market at a time; older pendings get marked
-- 'superseded' on re-enqueue so the queue stays tractable.
CREATE UNIQUE INDEX IF NOT EXISTS market_classification_review_one_pending
    ON market_classification_review (condition_id) WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS market_classification_review_pending_created
    ON market_classification_review (created_at DESC)
    WHERE status = 'pending';
