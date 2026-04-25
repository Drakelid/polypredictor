from __future__ import annotations

from api.markets import _is_crypto_market


def test_is_crypto_market_uses_category_tags_question_and_slug() -> None:
    assert _is_crypto_market(
        {
            "category": "Finance",
            "tags": ["ETF"],
            "question": "Will the spot Bitcoin ETF see inflows?",
            "slug": "btc-etf-inflows",
        }
    )
    assert _is_crypto_market(
        {
            "category": "Crypto",
            "tags": [],
            "question": "Will SOL close above $200?",
            "slug": "sol-above-200",
        }
    )
    assert not _is_crypto_market(
        {
            "category": "Politics",
            "tags": ["election"],
            "question": "Will a candidate win?",
            "slug": "candidate-win",
        }
    )
