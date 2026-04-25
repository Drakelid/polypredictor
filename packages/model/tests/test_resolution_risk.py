from __future__ import annotations

from model.resolution_risk import score_resolution_risk


def test_score_resolution_risk_flags_subjective_oracle_wording() -> None:
    result = score_resolution_risk(
        question="Will a major exchange outage occur by end of day?",
        description="Resolved according to reports on X and a single oracle statement.",
        resolution_source="",
    )

    assert result.is_flagged is True
    assert result.level == "high"
    assert result.multiplier > 1.0
    assert "no explicit resolution source" in result.reasons


def test_score_resolution_risk_keeps_clear_market_low_risk() -> None:
    result = score_resolution_risk(
        question="Will CPI print below 2.8% in June?",
        description="Resolves to the June CPI YoY release published by BLS.",
        resolution_source="https://www.bls.gov/schedule/news_release/cpi.htm",
    )

    assert result.level == "low"
    assert result.is_flagged is False
