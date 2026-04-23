from __future__ import annotations

import json

from api.discrete_inputs import resolve_discrete_record


def test_resolve_discrete_record_prefers_market_override(tmp_path) -> None:
    path = tmp_path / "discrete.json"
    path.write_text(
        json.dumps(
            {
                "families": {
                    "fomc": {"fedwatch_prob": 0.72, "source": "family"},
                },
                "markets": [
                    {
                        "slug": "fed-cuts-june-2026",
                        "event_family": "fomc",
                        "fedwatch_prob": 0.68,
                        "source": "market",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    record = resolve_discrete_record(
        path=str(path),
        event_family="fomc",
        condition_id=None,
        slug="fed-cuts-june-2026",
        question="Will the Fed cut in June 2026?",
    )

    assert record is not None
    assert record.fedwatch_prob == 0.68
    assert record.source == "market"


def test_resolve_discrete_record_falls_back_to_family(tmp_path) -> None:
    path = tmp_path / "discrete.json"
    path.write_text(
        json.dumps(
            {
                "families": {
                    "cpi": {
                        "consensus_point": 2.9,
                        "consensus_stddev": 0.2,
                        "source": "family",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    record = resolve_discrete_record(
        path=str(path),
        event_family="cpi",
        condition_id=None,
        slug="cpi-june-2026",
        question="Will CPI print below 2.8% in June?",
    )

    assert record is not None
    assert record.consensus_point == 2.9
    assert record.consensus_stddev == 0.2
