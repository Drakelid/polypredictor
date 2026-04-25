from __future__ import annotations

import json
from pathlib import Path

from api import polymarket_schema_watch as schema_watch
from model import response_shape_hash


def test_load_pinned_shapes_parses_well_formed_file(tmp_path: Path) -> None:
    pin_file = tmp_path / "pins.json"
    pin_file.write_text(
        json.dumps(
            [
                {"endpoint": "gamma:/markets", "shape_hash": "abc123", "version": "v1"},
                {"endpoint": "gamma:/events", "shape_hash": "def456"},
            ]
        ),
        encoding="utf-8",
    )
    pinned = schema_watch.load_pinned_shapes(pin_file)
    assert len(pinned) == 2
    assert pinned[0].endpoint == "gamma:/markets"
    assert pinned[0].shape_hash == "abc123"
    assert pinned[0].version == "v1"
    assert pinned[1].version is None


def test_load_pinned_shapes_returns_empty_when_file_missing(tmp_path: Path) -> None:
    assert schema_watch.load_pinned_shapes(tmp_path / "missing.json") == []


def test_load_pinned_shapes_skips_malformed_entries(tmp_path: Path) -> None:
    pin_file = tmp_path / "pins.json"
    pin_file.write_text(
        json.dumps(
            [
                {"endpoint": "gamma:/markets", "shape_hash": "abc"},
                {"endpoint": "", "shape_hash": "no-endpoint"},
                {"shape_hash": "no-key"},
                "not-a-dict",
            ]
        ),
        encoding="utf-8",
    )
    pinned = schema_watch.load_pinned_shapes(pin_file)
    assert [p.endpoint for p in pinned] == ["gamma:/markets"]


def test_evaluate_drift_clean_when_samples_match() -> None:
    sample = {"id": "abc", "volume": 100.0, "tags": ["btc"]}
    pinned = [
        schema_watch.PinnedShape(
            endpoint="gamma:/markets",
            shape_hash=response_shape_hash(sample),
        )
    ]
    records = schema_watch.evaluate_drift(pinned=pinned, samples={"gamma:/markets": sample})
    assert len(records) == 1
    assert records[0].drifted is False
    assert records[0].added_keys == ()


def test_evaluate_drift_flags_added_keys() -> None:
    pinned_sample = {"id": "abc", "volume": 100.0}
    drifted_sample = {"id": "abc", "volume": 100.0, "spread": 0.02, "depth": 1500.0}
    pinned = [
        schema_watch.PinnedShape(
            endpoint="gamma:/markets",
            shape_hash=response_shape_hash(pinned_sample),
        )
    ]
    records = schema_watch.evaluate_drift(
        pinned=pinned,
        samples={"gamma:/markets": drifted_sample},
    )
    assert records[0].drifted is True
    assert records[0].observed_hash != records[0].expected_hash


def test_evaluate_drift_records_missing_sample_as_drift() -> None:
    """A pinned endpoint with no sample must surface as drift so an operator
    can't silently lose coverage by removing the fetcher."""
    pinned = [
        schema_watch.PinnedShape(endpoint="gamma:/markets", shape_hash="abc123"),
    ]
    records = schema_watch.evaluate_drift(pinned=pinned, samples={})
    assert records[0].drifted is True
    assert records[0].observed_hash == ""
    assert "<no_sample>" in records[0].added_keys


def test_evaluate_drift_ignores_samples_without_pin() -> None:
    pinned: list[schema_watch.PinnedShape] = []
    records = schema_watch.evaluate_drift(
        pinned=pinned,
        samples={"gamma:/markets": {"id": "abc"}},
    )
    assert records == []


def test_evaluate_drift_attaches_pinned_version() -> None:
    sample = {"id": "abc"}
    pinned = [
        schema_watch.PinnedShape(
            endpoint="gamma:/markets",
            shape_hash=response_shape_hash(sample),
            version="v1.2.3",
        )
    ]
    records = schema_watch.evaluate_drift(pinned=pinned, samples={"gamma:/markets": sample})
    assert records[0].pinned_version == "v1.2.3"
