from __future__ import annotations

from datetime import UTC, datetime

import pytest
from api.long_tail_priors import (
    LongTailResolvedMarket,
    _cosine_similarity,
    load_long_tail_resolved_markets,
    long_tail_prior_context,
)
from model import ClassificationResult, MarketFeatures, MarketType


class _QueryResult:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.result_rows = rows


class _FakeClickHouse:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows
        self.queries: list[str] = []

    async def query(self, query: str, parameters: dict[str, object] | None = None) -> _QueryResult:
        self.queries.append(query)
        return _QueryResult(self.rows)


@pytest.mark.asyncio
async def test_load_long_tail_resolved_markets_shapes_rows() -> None:
    ch = _FakeClickHouse(
        [
            ("cond-1", "YES", "Will Coinbase be hacked?", "coinbase-hacked", ["crypto"], "", ""),
            ("cond-2", "NO", "Will Binance halt withdrawals?", "binance-withdrawals", ["crypto"], "", ""),
        ]
    )

    rows = await load_long_tail_resolved_markets(
        ch,
        asked_at=datetime(2026, 4, 22, tzinfo=UTC),
        limit=100,
    )

    assert len(rows) == 2
    assert rows[0].outcome == 1
    assert rows[1].outcome == 0
    assert "market_resolutions" in ch.queries[0]


def test_long_tail_prior_context_prefers_analog_similarity_then_base_rate() -> None:
    classification = ClassificationResult(
        market_type=MarketType.LONG_TAIL_BINARY,
        confidence=0.6,
        features=MarketFeatures(),
        reasons=["long-tail binary phrasing"],
    )
    context = long_tail_prior_context(
        current_condition_id="cond-current",
        question="Will Coinbase be hacked in 2026?",
        slug="coinbase-hacked-2026",
        tags=["crypto", "security"],
        classification=classification,
        candidates=[
            LongTailResolvedMarket(
                condition_id="cond-a",
                question="Will Coinbase be hacked in 2025?",
                slug="coinbase-hacked-2025",
                tags=["crypto", "security"],
                outcome=1,
                asset=None,
                event_family=None,
            ),
            LongTailResolvedMarket(
                condition_id="cond-b",
                question="Will Binance halt withdrawals in 2025?",
                slug="binance-withdrawals-2025",
                tags=["crypto", "exchange"],
                outcome=0,
                asset=None,
                event_family=None,
            ),
            LongTailResolvedMarket(
                condition_id="cond-c",
                question="Will Kraken suffer an outage?",
                slug="kraken-outage",
                tags=["crypto", "exchange"],
                outcome=0,
                asset=None,
                event_family=None,
            ),
        ],
    )

    assert context.embedding_prior is not None
    assert context.embedding_prior > 0.5
    assert context.historical_base_rate is not None
    assert 0.0 < context.historical_base_rate < 1.0


def test_cosine_similarity_zero_for_empty_vectors() -> None:
    assert _cosine_similarity({}, {}) == 0.0  # type: ignore[arg-type]
