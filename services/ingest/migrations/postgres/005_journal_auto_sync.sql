-- Journal auto-sync dedupe for Polymarket user WSS fills (M7.1)

ALTER TABLE journal_calls
    ADD COLUMN IF NOT EXISTS source_event_id TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS journal_calls_source_event_id_unique
    ON journal_calls (user_id, source, source_event_id)
    WHERE source_event_id IS NOT NULL;
