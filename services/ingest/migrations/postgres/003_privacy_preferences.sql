-- Cross-user learning opt-in (M7.4)

CREATE TABLE IF NOT EXISTS user_privacy_preferences (
    user_id                      UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    cross_user_learning_opt_in   BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'user_privacy_preferences_set_updated_at') THEN
        CREATE TRIGGER user_privacy_preferences_set_updated_at
            BEFORE UPDATE ON user_privacy_preferences
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    END IF;
END$$;
