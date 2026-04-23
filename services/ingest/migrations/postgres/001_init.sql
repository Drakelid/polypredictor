-- PolyPredictor Postgres schema (M0)
-- User-scoped data: accounts, journal, tuning profiles, encrypted API keys.
-- Time-series signal + price history lives in ClickHouse.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Users ----------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           CITEXT UNIQUE,
    display_name    TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- CITEXT may not exist in all builds; fall back to LOWER() UNIQUE if absent.
-- (docker-entrypoint will continue on error for CREATE EXTENSION.)
CREATE EXTENSION IF NOT EXISTS "citext";

-- Encrypted API keys (Polymarket CLOB keys for F7 user WSS journal auto-sync)
-- Ciphertext is supplied by the app using a KMS-derived DEK; DB never sees plaintext.
CREATE TABLE IF NOT EXISTS user_api_keys_encrypted (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider        TEXT NOT NULL CHECK (provider IN ('polymarket_clob')),
    -- Opaque ciphertext blobs. Server decrypts only for outbound API calls.
    api_key_ct      BYTEA NOT NULL,
    api_secret_ct   BYTEA NOT NULL,
    passphrase_ct   BYTEA NOT NULL,
    proxy_wallet    TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    rotated_at      TIMESTAMPTZ,
    UNIQUE (user_id, provider)
);

-- Journal --------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS journal_calls (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                 UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    condition_id            TEXT NOT NULL,
    token_id                TEXT NOT NULL,
    outcome                 TEXT NOT NULL CHECK (outcome IN ('YES', 'NO')),
    side                    TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    size_usdc               NUMERIC(20, 6) NOT NULL,
    entry_price             NUMERIC(10, 6) NOT NULL,
    -- Model snapshot captured at call time for later calibration scoring (F7).
    model_prob_at_call      NUMERIC(10, 6) NOT NULL,
    model_band_lo_at_call   NUMERIC(10, 6) NOT NULL,
    model_band_hi_at_call   NUMERIC(10, 6) NOT NULL,
    market_mid_at_call      NUMERIC(10, 6) NOT NULL,
    source                  TEXT NOT NULL CHECK (source IN ('manual', 'auto_wss', 'auto_poll')),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Resolution fields populated when the market settles.
    resolved_outcome        TEXT CHECK (resolved_outcome IN ('YES', 'NO', 'INVALID')),
    resolved_at             TIMESTAMPTZ,
    pnl_usdc                NUMERIC(20, 6),
    brier_contribution      NUMERIC(10, 8)
);

CREATE INDEX IF NOT EXISTS journal_calls_user_created
    ON journal_calls (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS journal_calls_condition
    ON journal_calls (condition_id);

-- Tuning profiles (F5) -------------------------------------------------------

CREATE TABLE IF NOT EXISTS tuning_profiles (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    preset          TEXT CHECK (preset IN ('conservative', 'balanced', 'aggressive', 'custom')),
    -- Additive log-odds shift per signal family (JSONB map). Never adjusts learned model weights directly.
    log_odds_shifts JSONB NOT NULL DEFAULT '{}'::jsonb,
    is_active       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, name)
);

-- Only one profile per user can be active at a time.
CREATE UNIQUE INDEX IF NOT EXISTS tuning_profiles_one_active_per_user
    ON tuning_profiles (user_id) WHERE is_active;

-- Polymarket address linkage (read-only tracking for F7) ---------------------

CREATE TABLE IF NOT EXISTS user_polymarket_addresses (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    proxy_wallet    TEXT NOT NULL,
    verified_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, proxy_wallet)
);

-- Updated-at trigger ---------------------------------------------------------

CREATE OR REPLACE FUNCTION set_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'users_set_updated_at') THEN
        CREATE TRIGGER users_set_updated_at
            BEFORE UPDATE ON users
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'tuning_profiles_set_updated_at') THEN
        CREATE TRIGGER tuning_profiles_set_updated_at
            BEFORE UPDATE ON tuning_profiles
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    END IF;
END$$;
