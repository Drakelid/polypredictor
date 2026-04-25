-- M5: perpetual funding + basis snapshots.
--
-- One row per (venue, asset, observed_at), append-only. Backtests use
-- ``observed_at <= asked_at`` so the funding/basis the model saw at training
-- time is reproducible. Today only Binance USDT perps are populated; Coinbase
-- derivatives can land in the same shape with venue='coinbase'.
--
-- ``basis_bps`` = (mark - index) / index * 10_000. Positive basis = mark
-- trading rich vs spot index (long premium); negative basis = mark cheap vs
-- spot (short premium / contango unwind).
--
-- ``funding_rate_annualized`` is the as-emitted funding rate scaled to a
-- yearly figure assuming 8h funding cycles (3 cycles/day × 365 days). It is
-- a derived convenience column so the dashboard can render funding without
-- re-applying the multiplier.

USE polypredictor;

CREATE TABLE IF NOT EXISTS perp_funding_basis (
    venue                    LowCardinality(String),
    asset                    LowCardinality(String),
    symbol                   String,
    index_price              Float64,
    mark_price               Float64,
    basis_bps                Float64,
    funding_rate             Nullable(Float64),
    funding_rate_annualized  Nullable(Float64),
    event_time               DateTime64(3, 'UTC'),
    observed_at              DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(observed_at)
PARTITION BY toYYYYMM(observed_at)
ORDER BY (venue, asset, observed_at)
TTL toDate(observed_at) + INTERVAL 2 YEAR;
