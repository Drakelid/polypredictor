-- Push / alert preferences (M3.5)

CREATE TABLE IF NOT EXISTS user_push_preferences (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    email_enabled       BOOLEAN NOT NULL DEFAULT FALSE,
    email_to            TEXT,
    webhook_enabled     BOOLEAN NOT NULL DEFAULT FALSE,
    webhook_url         TEXT,
    min_severity        NUMERIC(10, 4) NOT NULL DEFAULT 1.0,
    event_types         TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    condition_ids       TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id),
    CHECK (min_severity >= 0),
    CHECK (NOT webhook_enabled OR webhook_url IS NOT NULL),
    CHECK (NOT email_enabled OR email_to IS NOT NULL)
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'user_push_preferences_set_updated_at'
    ) THEN
        CREATE TRIGGER user_push_preferences_set_updated_at
            BEFORE UPDATE ON user_push_preferences
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    END IF;
END$$;
