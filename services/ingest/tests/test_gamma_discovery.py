from __future__ import annotations

from ingest.workers.gamma_discovery import _multi_outcome_sibling_counts
from polymarket_client import Market


def _market(condition_id: str, *, event_id: str | None) -> Market:
    return Market.model_validate(
        {
            "conditionId": condition_id,
            "eventId": event_id,
            "question": f"Question {condition_id}",
            "slug": condition_id,
        }
    )


def test_multi_outcome_sibling_counts_groups_by_event_id() -> None:
    counts = _multi_outcome_sibling_counts(
        [
            _market("cond-a", event_id="evt-1"),
            _market("cond-b", event_id="evt-1"),
            _market("cond-c", event_id="evt-2"),
            _market("cond-d", event_id=None),
        ]
    )

    assert counts == {"evt-1": 2, "evt-2": 1}
