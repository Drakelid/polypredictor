-- PolyPredictor ClickHouse schema (M0)
--
-- Point-in-time correctness (PRD §5.4, §6.6):
--   Every row carries BOTH:
--     - event_time:   when the thing happened in the real world
--     - observed_at:  when *we* learned about it (the as-of key for PIT queries)
--   Historical queries (training, backtest, calibration) filter by observed_at <= T.
--   Revisions (deleted tweets, UMA re-resolutions) are new rows, never UPDATE.
--
-- We use ReplacingMergeTree keyed on (observed_at, <natural key>) so that
-- identical rows re-ingested are collapsed, but versioned edits create new rows.
-- ORDER BY puts the filter columns first for efficient PIT scans.

CREATE DATABASE IF NOT EXISTS polypredictor;
USE polypredictor;

-- Markets catalog snapshots (from Gamma /markets) ----------------------------
-- One row per (condition_id, observed_at); append-only.

CREATE TABLE IF NOT EXISTS markets_snapshots (
    condition_id        String,
    event_id            String,
    question            String,
    description         String,
    slug                String,
    category            LowCardinality(String),
    tags                Array(String),
    active              UInt8,
    closed              UInt8,
    archived            UInt8,
    volume_usdc         Float64,
    liquidity_usdc      Float64,
    open_interest_usdc  Float64,
    end_date            Nullable(DateTime64(3, 'UTC')),
    resolution_source   String,
    token_ids           Array(String),  -- [yes_token_id, no_token_id] for binaries
    event_time          DateTime64(3, 'UTC'),
    observed_at         DateTime64(3, 'UTC'),
    payload_hash        String          -- sha256 of raw payload for dedup
)
ENGINE = ReplacingMergeTree(observed_at)
PARTITION BY toYYYYMM(observed_at)
ORDER BY (condition_id, observed_at, payload_hash)
TTL toDate(observed_at) + INTERVAL 2 YEAR;

-- Midpoint / price snapshots (from CLOB /midpoint, /price) -------------------

CREATE TABLE IF NOT EXISTS market_quotes (
    token_id            String,
    condition_id        String,
    mid                 Float64,
    best_bid            Float64,
    best_ask            Float64,
    bid_size            Float64,
    ask_size            Float64,
    spread              Float64,
    event_time          DateTime64(3, 'UTC'),
    observed_at         DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(observed_at)
PARTITION BY toYYYYMM(observed_at)
ORDER BY (token_id, observed_at)
TTL toDate(observed_at) + INTERVAL 2 YEAR;

-- Level-2 orderbook snapshots & deltas (CLOB /book + WSS deltas) -------------

CREATE TABLE IF NOT EXISTS orderbook_snapshots (
    token_id            String,
    condition_id        String,
    side                Enum8('bid' = 1, 'ask' = 2),
    price               Float64,
    size                Float64,
    event_time          DateTime64(3, 'UTC'),
    observed_at         DateTime64(3, 'UTC'),
    source              Enum8('rest' = 1, 'wss_init' = 2, 'wss_delta' = 3),
    sequence            UInt64 DEFAULT 0
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(observed_at)
ORDER BY (token_id, observed_at, side, price)
TTL toDate(observed_at) + INTERVAL 2 YEAR;

-- Public trades (CLOB /trades + WSS trade events) ----------------------------

CREATE TABLE IF NOT EXISTS trades_raw (
    trade_id            String,
    token_id            String,
    condition_id        String,
    price               Float64,
    size                Float64,
    side                Enum8('buy' = 1, 'sell' = 2),
    -- Aggressor (taker) side derived at write time if possible; null otherwise.
    aggressor_side      Nullable(Enum8('buy' = 1, 'sell' = 2)),
    crossed_spread      Nullable(UInt8),
    event_time          DateTime64(3, 'UTC'),
    observed_at         DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(observed_at)
PARTITION BY toYYYYMM(observed_at)
ORDER BY (token_id, trade_id, observed_at)
TTL toDate(observed_at) + INTERVAL 2 YEAR;

-- Price history OHLC (CLOB /prices-history) ---------------------------------

CREATE TABLE IF NOT EXISTS prices_history (
    token_id            String,
    condition_id        String,
    interval_bucket     Enum8('1h' = 1, '6h' = 2, '1d' = 3),
    bucket_start        DateTime64(3, 'UTC'),
    open                Float64,
    high                Float64,
    low                 Float64,
    close               Float64,
    volume              Float64,
    event_time          DateTime64(3, 'UTC'),
    observed_at         DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(observed_at)
PARTITION BY toYYYYMM(observed_at)
ORDER BY (token_id, interval_bucket, bucket_start, observed_at)
TTL toDate(observed_at) + INTERVAL 2 YEAR;

-- Holders snapshots (Data /holders) ------------------------------------------

CREATE TABLE IF NOT EXISTS holders_snapshots (
    condition_id        String,
    token_id            String,
    rank                UInt16,
    proxy_wallet        String,
    size                Float64,
    pct_of_outcome      Float32,
    event_time          DateTime64(3, 'UTC'),
    observed_at         DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(observed_at)
PARTITION BY toYYYYMM(observed_at)
ORDER BY (condition_id, token_id, rank, observed_at)
TTL toDate(observed_at) + INTERVAL 2 YEAR;

-- Smart-money positions snapshots (Data /positions for leaderboard wallets) --

CREATE TABLE IF NOT EXISTS positions_smart_money (
    proxy_wallet        String,
    condition_id        String,
    token_id            String,
    outcome             Enum8('YES' = 1, 'NO' = 2),
    size                Float64,
    avg_entry_price     Float64,
    current_value_usdc  Float64,
    -- Leaderboard context at snapshot time.
    leaderboard_rank    Nullable(UInt32),
    leaderboard_pnl     Nullable(Float64),
    leaderboard_vol     Nullable(Float64),
    event_time          DateTime64(3, 'UTC'),
    observed_at         DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(observed_at)
PARTITION BY toYYYYMM(observed_at)
ORDER BY (proxy_wallet, condition_id, token_id, observed_at)
TTL toDate(observed_at) + INTERVAL 2 YEAR;

-- Market resolutions (Gamma /markets + UMA) ----------------------------------
-- Append-only. Revised resolutions are NEW rows, never UPDATE.

CREATE TABLE IF NOT EXISTS market_resolutions (
    condition_id        String,
    resolved_outcome    Enum8('YES' = 1, 'NO' = 2, 'INVALID' = 3, 'REVISED' = 4),
    resolution_source   LowCardinality(String),  -- 'uma', 'manual', etc.
    is_revision         UInt8,                    -- 1 if this supersedes a prior resolution
    prior_observed_at   Nullable(DateTime64(3, 'UTC')),  -- pointer to the row being revised
    dispute_status      LowCardinality(String),
    event_time          DateTime64(3, 'UTC'),
    observed_at         DateTime64(3, 'UTC')
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(observed_at)
ORDER BY (condition_id, observed_at);

-- Social / news / on-chain / macro events ------------------------------------
-- Immutable history: deleted tweets or revised headlines become new rows with
-- event_kind='retraction'. Source URI + payload hash identify the original.

CREATE TABLE IF NOT EXISTS external_events (
    event_kind          LowCardinality(String),  -- 'tweet', 'news', 'onchain', 'macro', 'retraction'
    source              LowCardinality(String),  -- 'x', 'reddit', 'rss:coindesk', 'glassnode', 'fred', ...
    source_uri          String,                   -- canonical URL or id
    source_id           String,                   -- native id (tweet_id, guid, tx hash)
    related_markets     Array(String),            -- condition_ids this event is tagged to
    author              String,
    title               String,
    body                String,
    url                 String,
    metadata            String,                   -- JSON blob (engagement, chain info, etc.)
    payload_hash        String,
    event_time          DateTime64(3, 'UTC'),
    observed_at         DateTime64(3, 'UTC')
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(observed_at)
ORDER BY (event_kind, source, event_time, observed_at)
TTL toDate(observed_at) + INTERVAL 2 YEAR;

CREATE INDEX IF NOT EXISTS external_events_source_id_idx ON external_events (source_id) TYPE bloom_filter GRANULARITY 4;

-- Source-health metrics ------------------------------------------------------

CREATE TABLE IF NOT EXISTS ingest_health (
    source              LowCardinality(String),
    endpoint            String,
    status              Enum8('ok' = 1, 'rate_limited' = 2, 'error' = 3, 'timeout' = 4),
    latency_ms          UInt32,
    http_status         UInt16,
    error_message       String,
    event_time          DateTime64(3, 'UTC'),
    observed_at         DateTime64(3, 'UTC')
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(observed_at)
ORDER BY (source, endpoint, observed_at)
TTL toDate(observed_at) + INTERVAL 180 DAY;
