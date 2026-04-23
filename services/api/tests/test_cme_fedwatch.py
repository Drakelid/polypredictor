from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx
from api.cme_fedwatch import (
    CMEFedWatchClient,
    _parse_fomc_action,
    _parse_meeting_hint,
    _probability_for_action,
)


@pytest.mark.asyncio
@respx.mock
async def test_fedwatch_client_resolves_any_cut_probability() -> None:
    respx.post("https://auth.cmegroup.com/as/token.oauth2").mock(
        return_value=httpx.Response(
            200,
            json={"access_token": "token-1", "token_type": "bearer", "expires_in": 1800},
        )
    )
    meetings_route = respx.get("https://markets.api.cmegroup.com/fedwatch/v1/meetings/future").mock(
        return_value=httpx.Response(
            200,
            json={
                "content": [
                    {"meetingDt": "2026-06-17", "offsetDayCount": 56},
                    {"meetingDt": "2026-07-29", "offsetDayCount": 98},
                ]
            },
        )
    )
    history_route = respx.get("https://markets.api.cmegroup.com/fedwatch/v1/meetings/history").mock(
        return_value=httpx.Response(
            200,
            json={
                "content": [
                    {
                        "meetingDt": "2026-03-18",
                        "lowerRt": 425,
                        "upperRt": 450,
                    }
                ]
            },
        )
    )
    forecasts_route = respx.get("https://markets.api.cmegroup.com/fedwatch/v1/forecasts").mock(
        return_value=httpx.Response(
            200,
            json={
                "content": [
                    {
                        "meetingDt": "2026-06-17",
                        "reportingDt": "2026-04-22",
                        "rateRange": [
                            {"lowerRt": 400, "upperRt": 425, "probability": 0.61},
                            {"lowerRt": 425, "upperRt": 450, "probability": 0.39},
                        ],
                    }
                ]
            },
        )
    )

    async with CMEFedWatchClient(
        api_id="id",
        api_secret="secret",
        application_name="PolyPredictor",
        application_vendor="PolyPredictor",
        application_version="0.0.1",
        user_agent="PolyPredictor/0.0.1",
        cache_ttl_s=60.0,
    ) as client:
        record = await client.implied_record_for_market(
            question="Will the Fed cut in June 2026?",
            slug="fed-cuts-june-2026",
            now=datetime(2026, 4, 22, tzinfo=UTC),
        )

    assert record is not None
    assert record.fedwatch_prob == pytest.approx(0.61)
    assert record.source == "cme_fedwatch:cut"
    assert record.as_of == "2026-04-22"
    assert meetings_route.call_count == 1
    assert history_route.call_count == 1
    assert forecasts_route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_fedwatch_client_exact_25bp_cut_uses_exact_bucket() -> None:
    respx.post("https://auth.cmegroup.com/as/token.oauth2").mock(
        return_value=httpx.Response(
            200,
            json={"access_token": "token-1", "token_type": "bearer", "expires_in": 1800},
        )
    )
    respx.get("https://markets.api.cmegroup.com/fedwatch/v1/meetings/future").mock(
        return_value=httpx.Response(
            200,
            json={"content": [{"meetingDt": "2026-06-17", "offsetDayCount": 56}]},
        )
    )
    respx.get("https://markets.api.cmegroup.com/fedwatch/v1/meetings/history").mock(
        return_value=httpx.Response(
            200,
            json={"content": [{"meetingDt": "2026-03-18", "lowerRt": 425, "upperRt": 450}]},
        )
    )
    respx.get("https://markets.api.cmegroup.com/fedwatch/v1/forecasts").mock(
        return_value=httpx.Response(
            200,
            json={
                "content": [
                    {
                        "meetingDt": "2026-06-17",
                        "reportingDt": "2026-04-22",
                        "rateRange": [
                            {"lowerRt": 400, "upperRt": 425, "probability": 0.55},
                            {"lowerRt": 375, "upperRt": 400, "probability": 0.1},
                            {"lowerRt": 425, "upperRt": 450, "probability": 0.35},
                        ],
                    }
                ]
            },
        )
    )

    async with CMEFedWatchClient(api_id="id", api_secret="secret") as client:
        record = await client.implied_record_for_market(
            question="Will the Fed cut 25bps at the June FOMC?",
            now=datetime(2026, 4, 22, tzinfo=UTC),
        )

    assert record is not None
    assert record.fedwatch_prob == pytest.approx(0.55)


def test_parse_fomc_action_and_meeting_hint() -> None:
    action = _parse_fomc_action("Will the Fed hike 50bps at the September 2026 FOMC?")
    assert action is not None
    assert action.label == "hike"
    assert action.bps == 50

    hint = _parse_meeting_hint(
        "Will the Fed hike 50bps at the September 2026 FOMC?",
        now=datetime(2026, 4, 22, tzinfo=UTC),
    )
    assert hint == (2026, 9)


def test_probability_for_action_handles_hold_and_any_move() -> None:
    from api.cme_fedwatch import FedWatchRateRange

    ranges = [
        FedWatchRateRange(lower_rt=400, upper_rt=425, probability=0.2),
        FedWatchRateRange(lower_rt=425, upper_rt=450, probability=0.7),
        FedWatchRateRange(lower_rt=450, upper_rt=475, probability=0.1),
    ]
    hold_action = _parse_fomc_action("Will the Fed hold?")
    cut_action = _parse_fomc_action("Will the Fed cut?")
    assert hold_action is not None
    assert cut_action is not None

    hold = _probability_for_action(
        ranges,
        current_lower=425,
        current_upper=450,
        action=hold_action,
    )
    cut = _probability_for_action(
        ranges,
        current_lower=425,
        current_upper=450,
        action=cut_action,
    )

    assert hold == pytest.approx(0.7)
    assert cut == pytest.approx(0.2)
