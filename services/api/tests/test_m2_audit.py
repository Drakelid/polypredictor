from __future__ import annotations

from datetime import UTC, datetime

import pytest
from api import m2_audit
from api.classifications import ClassificationRow
from api.journal import JournalSummary
from model import MarketFeatures, MarketType


def _classification_row(
    condition_id: str,
    market_type: MarketType,
) -> ClassificationRow:
    return ClassificationRow(
        condition_id=condition_id,
        market_type=market_type,
        confidence=0.9,
        needs_review=False,
        reviewer_flag="",
        classifier="regex_v1",
        features=MarketFeatures(),
        reasons=[],
        event_time=datetime(2026, 4, 22, tzinfo=UTC),
        observed_at=datetime(2026, 4, 22, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_typed_render_coverage_counts_complete_detail_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snaps = [
        {
            "condition_id": "cond-threshold",
            "question": "Will BTC be above $150,000 by Dec 31, 2026?",
            "slug": "btc-150k-2026",
            "tags": ["crypto"],
            "end_date": datetime(2026, 12, 31, tzinfo=UTC),
        },
        {
            "condition_id": "cond-misc",
            "question": "Will something vague happen?",
            "slug": "misc",
            "tags": [],
            "end_date": datetime(2026, 12, 31, tzinfo=UTC),
        },
    ]

    async def _fake_latest_markets(ch: object, asked_at: datetime, *, limit: int):
        return snaps

    async def _fake_classifications(ch: object, condition_ids: list[str], asked_at: datetime):
        return {
            "cond-threshold": _classification_row("cond-threshold", MarketType.THRESHOLD),
            "cond-misc": _classification_row("cond-misc", MarketType.MISC),
        }

    async def _fake_model_for_market(ch: object, *, condition_id: str, asked_at: datetime):
        if condition_id != "cond-threshold":
            return None
        return type(
            "Detail",
            (),
            {
                "model_prob": 0.61,
                "band_lo": 0.51,
                "band_hi": 0.71,
                "driver_summaries": ["Baseline prior pushed the ensemble higher."],
                "feature_attributions": [{"feature_name": "p_base_logit"}],
            },
        )()

    monkeypatch.setattr(m2_audit, "_latest_markets", _fake_latest_markets)
    monkeypatch.setattr(m2_audit, "classifications_batch_asof", _fake_classifications)
    monkeypatch.setattr(m2_audit, "model_for_market", _fake_model_for_market)

    report = await m2_audit.typed_render_coverage(
        object(),
        asked_at=datetime(2026, 4, 22, tzinfo=UTC),
        limit=100,
    )

    assert report.total_typed == 1
    assert report.fully_rendered == 1
    assert report.fully_rendered_ratio == pytest.approx(1.0)
    assert report.by_type == {"threshold": 1}
    assert report.by_type_fully_rendered == {"threshold": 1}


@pytest.mark.asyncio
async def test_empirical_conformal_coverage_groups_by_type_and_ttr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved_at = datetime(2026, 4, 22, 12, tzinfo=UTC)

    async def _fake_resolutions(
        ch: object,
        *,
        resolved_from: datetime | None,
        resolved_to: datetime | None,
        limit: int,
    ):
        return [("cond-1", "YES", resolved_at)]

    async def _fake_model_for_market(ch: object, *, condition_id: str, asked_at: datetime):
        hours = round((resolved_at - asked_at).total_seconds() / 3600)
        if hours == 3:
            return type(
                "Detail",
                (),
                {
                    "market_type": "threshold",
                    "model_prob": 0.62,
                    "band_lo": 0.55,
                    "band_hi": 1.0,
                    "band_coverage": 0.8,
                },
            )()
        return type(
            "Detail",
            (),
            {
                "market_type": "threshold",
                "model_prob": 0.62,
                "band_lo": 0.10,
                "band_hi": 0.40,
                "band_coverage": 0.8,
            },
        )()

    monkeypatch.setattr(m2_audit, "latest_binary_resolutions", _fake_resolutions)
    monkeypatch.setattr(m2_audit, "model_for_market", _fake_model_for_market)

    report = await m2_audit.empirical_conformal_coverage(
        object(),
        asked_at=datetime(2026, 4, 23, tzinfo=UTC),
        resolved_limit=10,
        horizons_hours=(3, 12),
    )

    assert report.total_resolved_markets == 1
    assert report.total_scored_samples == 2
    cells = {(cell.market_type, cell.ttr_bucket): cell for cell in report.by_cell}
    assert cells[("threshold", "lt_6h")].empirical_coverage == pytest.approx(1.0)
    assert cells[("threshold", "6h_24h")].empirical_coverage == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_journal_resolved_audit_checks_minimum(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_summary(*, pool: object, ch: object, settings: object) -> JournalSummary:
        return JournalSummary(
            total_calls=24,
            resolved_calls=21,
            unresolved_calls=3,
            avg_brier=0.11,
            total_pnl_usdc=42.0,
            confidence_buckets=[],
            best_calls=[],
            worst_calls=[],
            edge_scatter=[],
            calibration_points=[],
        )

    monkeypatch.setattr(m2_audit, "journal_summary", _fake_summary)

    report = await m2_audit.journal_resolved_audit(
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        settings=object(),
    )

    assert report.total_calls == 24
    assert report.resolved_calls == 21
    assert report.meets_minimum is True
