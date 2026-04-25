-- Magic-link token storage for the email-link auth flow (M0.6).
--
-- Tokens are short-lived (default 15 minutes) and one-shot: ``used_at`` is
-- set the first time a token is verified, and subsequent verifications
-- against the same token must reject. The application is responsible for
-- generating cryptographically random token values; the database only
-- stores them, the expiry, and the consumption marker.
--
-- We deliberately do NOT foreign-key to ``users(id)`` here — magic links
-- are issued by email address before the user record may exist (sign-up
-- flow). The application materializes the user row on first successful
-- verification.

CREATE TABLE IF NOT EXISTS auth_magic_links (
    token       TEXT PRIMARY KEY,
    email       CITEXT NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    used_at     TIMESTAMPTZ
);

-- Lookups are exclusively by token (PK already covers it) but a dropper job
-- that purges expired/used rows benefits from an expiry index.
CREATE INDEX IF NOT EXISTS auth_magic_links_expires_at
    ON auth_magic_links (expires_at);
CREATE INDEX IF NOT EXISTS auth_magic_links_email
    ON auth_magic_links (email);
