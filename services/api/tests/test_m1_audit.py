from __future__ import annotations

from datetime import UTC, datetime

import pytest
from api import m1_audit
from api.classifications import ClassificationRow
from api.m1_audit import active_type_coverage, sample_threshold_inputs
from model import DeribitIV, MarketFeatures, MarketType


def _classification_row(
    condition_id: str,
    market_type: MarketType,
    *,
    confidence: float = 0.9,
    needs_review: bool = False,
    features: MarketFeatures | None = None,
) -> ClassificationRow:
    return ClassificationRow(
        condition_id=condition_id,
        market_type=market_type,
        confidence=confidence,
        needs_review=needs_review,
        reviewer_flag="pending" if needs_review else "",
        classifier="regex_v1",
        features=features or MarketFeatures(),
        reasons=[],
        event_time=datetime(2026, 4, 22, tzinfo=UTC),
        observed_at=datetime(2026, 4, 22, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_active_type_coverage_counts_effective_non_misc(monkeypatch: pytest.MonkeyPatch) -> None:
    snaps = [
        {
            "condition_id": "cond-threshold",
            "question": "Will BTC be above $150,000 by Dec 31, 2026?",
            "slug": "btc-150k-2026",
            "tags": ["crypto"],
            "end_date": datetime(2026, 12, 31, tzinfo=UTC),
            "token_ids": ["tok-1"],
        },
        {
            "condition_id": "cond-misc",
            "question": "Will this vague thing happen?",
            "slug": "vague",
            "tags": [],
            "end_date": datetime(2026, 12, 31, tzinfo=UTC),
            "token_ids": ["tok-2"],
        },
        {
            "condition_id": "cond-range",
            "question": "Will ETH end June 2026 between $3,000 and $3,500?",
            "slug": "eth-range",
            "tags": ["crypto"],
            "end_date": datetime(2026, 6, 30, tzinfo=UTC),
            "token_ids": ["tok-3"],
        },
    ]

    async def _fake_latest_markets(ch: object, asked_at: datetime, *, limit: int) -> list[dict[str, object]]:
        assert limit == 100
        return snaps

    async def _fake_classifications_batch_asof(
        ch: object, condition_ids: list[str], asked_at: datetime
    ) -> dict[str, ClassificationRow]:
        assert condition_ids == ["cond-threshold", "cond-misc", "cond-range"]
        return {
            "cond-misc": _classification_row(
                "cond-misc",
                MarketType.MISC,
                confidence=0.3,
                needs_review=True,
            )
        }

    async def _fake_multi_outcome_contexts(
        ch: object, snaps_: list[dict[str, object]], asked_at: datetime
    ) -> dict[str, object]:
        return {}

    async def _fake_latest_mids(
        ch: object, token_ids: list[str], asked_at: datetime
    ) -> dict[str, float]:
        return {}

    monkeypatch.setattr(m1_audit, "_latest_markets", _fake_latest_markets)
    monkeypatch.setattr(m1_audit, "classifications_batch_asof", _fake_classifications_batch_asof)
    monkeypatch.setattr(m1_audit, "_multi_outcome_contexts_for_markets", _fake_multi_outcome_contexts)
    monkeypatch.setattr(m1_audit, "_latest_mids_for_tokens", _fake_latest_mids)

    coverage = await active_type_coverage(
        object(),
        asked_at=datetime(2026, 4, 22, tzinfo=UTC),
        limit=100,
    )

    assert coverage.total_active == 3
    assert coverage.non_misc_active == 2
    assert coverage.non_misc_ratio == pytest.approx(2 / 3)
    assert coverage.needs_review_active == 1
    assert coverage.by_type == {
        "misc": 1,
        "range": 1,
        "threshold": 1,
    }


class _FakeDeribit:
    def __init__(self, iv: DeribitIV | None) -> None:
        self.iv = iv
        self.calls: list[tuple[str, float, str, float]] = []

    async def fetch_iv(
        self,
        *,
        currency: str,
        strike: float,
        direction: str,
        horizon_days: float,
    ) -> DeribitIV | None:
        self.calls.append((currency, strike, direction, horizon_days))
        return self.iv


@pytest.mark.asyncio
async def test_sample_threshold_inputs_returns_live_iv_details(monkeypatch: pytest.MonkeyPatch) -> None:
    snap = {
        "condition_id": "cond-threshold",
        "question": "Will BTC be above $150,000 by Jul 31, 2026?",
        "slug": "btc-150k-july",
        "tags": ["crypto"],
        "end_date": datetime(2026, 7, 31, tzinfo=UTC),
        "token_ids": ["tok-yes", "tok-no"],
    }
    classification = _classification_row(
        "cond-threshold",
        MarketType.THRESHOLD,
        features=MarketFeatures(
            asset="BTC",
            strike=150_000,
            direction="above",
            resolution_date=datetime(2026, 7, 31, tzinfo=UTC),
        ),
    )
    fake_deribit = _FakeDeribit(
        DeribitIV(
            index_price=103_000,
            expiry_days=98,
            realized_vol=0.41,
            atm_iv=0.58,
            strike_iv=0.67,
            atm_instrument="BTC-31JUL26-100000-C",
            strike_instrument="BTC-31JUL26-150000-C",
        )
    )

    async def _fake_latest_markets(ch: object, asked_at: datetime, *, limit: int) -> list[dict[str, object]]:
        return [snap]

    async def _fake_classifications_batch_asof(
        ch: object, condition_ids: list[str], asked_at: datetime
    ) -> dict[str, ClassificationRow]:
        return {"cond-threshold": classification}

    async def _fake_multi_outcome_contexts(
        ch: object, snaps_: list[dict[str, object]], asked_at: datetime
    ) -> dict[str, object]:
        return {}

    async def _fake_latest_mids(
        ch: object, token_ids: list[str], asked_at: datetime
    ) -> dict[str, float]:
        return {"tok-yes": 0.44}

    monkeypatch.setattr(m1_audit, "_latest_markets", _fake_latest_markets)
    monkeypatch.setattr(m1_audit, "classifications_batch_asof", _fake_classifications_batch_asof)
    monkeypatch.setattr(m1_audit, "_multi_outcome_contexts_for_markets", _fake_multi_outcome_contexts)
    monkeypatch.setattr(m1_audit, "_latest_mids_for_tokens", _fake_latest_mids)

    checks = await sample_threshold_inputs(
        object(),
        asked_at=datetime(2026, 4, 22, tzinfo=UTC),
        active_limit=100,
        sample_limit=5,
        deribit=fake_deribit,
    )

    assert len(checks) == 1
    check = checks[0]
    assert check.status == "ok"
    assert check.market_mid == pytest.approx(0.44)
    assert check.asset == "BTC"
    assert check.strike == pytest.approx(150_000)
    assert check.index_price == pytest.approx(103_000)
    assert check.realized_vol == pytest.approx(0.41)
    assert check.atm_iv == pytest.approx(0.58)
    assert check.strike_iv == pytest.approx(0.67)
    assert check.atm_instrument == "BTC-31JUL26-100000-C"
    assert check.strike_instrument == "BTC-31JUL26-150000-C"
    assert fake_deribit.calls and fake_deribit.calls[0][0] == "BTC"
