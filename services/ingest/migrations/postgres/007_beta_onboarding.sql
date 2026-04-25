CREATE TABLE IF NOT EXISTS beta_invites (
  id UUID PRIMARY KEY,
  email CITEXT UNIQUE NOT NULL,
  display_name TEXT,
  status TEXT NOT NULL DEFAULT 'invited'
    CHECK (status IN ('invited', 'accepted', 'revoked')),
  invited_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  accepted_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_beta_invites_status
  ON beta_invites (status, invited_at DESC);

CREATE TABLE IF NOT EXISTS beta_feedback (
  id UUID PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('bug', 'idea', 'model', 'data', 'other')),
  message TEXT NOT NULL,
  page_url TEXT,
  condition_id TEXT,
  contact_email CITEXT,
  submitted_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_beta_feedback_submitted_at
  ON beta_feedback (submitted_at DESC);

CREATE INDEX IF NOT EXISTS idx_beta_feedback_kind
  ON beta_feedback (kind, submitted_at DESC);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'beta_invites_set_updated_at') THEN
        CREATE TRIGGER beta_invites_set_updated_at
            BEFORE UPDATE ON beta_invites
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    END IF;
END$$;
