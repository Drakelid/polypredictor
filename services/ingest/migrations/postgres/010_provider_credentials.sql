-- Allow the settings UI to store operator/user provider credentials in the
-- existing encrypted credential table. Values remain ciphertext-only.

ALTER TABLE user_api_keys_encrypted
    DROP CONSTRAINT IF EXISTS user_api_keys_encrypted_provider_check;

ALTER TABLE user_api_keys_encrypted
    ADD CONSTRAINT user_api_keys_encrypted_provider_check
    CHECK (
        provider IN (
            'polymarket_clob',
            'cme_fedwatch',
            'x_api',
            'reddit',
            'glassnode',
            'dune',
            'stripe'
        )
    );
