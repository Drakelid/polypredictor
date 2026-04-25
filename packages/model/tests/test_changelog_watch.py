from __future__ import annotations

from model import (
    compare_response_shape,
    diff_shapes,
    is_newer_version,
    response_shape_hash,
)


def test_is_newer_version_handles_pep440_strings() -> None:
    assert is_newer_version("1.2.0", "1.1.9") is True
    assert is_newer_version("1.0.0", "1.0.0") is False
    assert is_newer_version("0.9.0", "1.0.0") is False


def test_response_shape_hash_independent_of_value_changes() -> None:
    a = {"id": "abc", "volume": 100.0, "tags": ["btc"]}
    b = {"id": "xyz", "volume": 999.99, "tags": ["eth", "sol"]}
    assert response_shape_hash(a) == response_shape_hash(b)


def test_response_shape_hash_changes_when_key_added_or_removed() -> None:
    base = {"id": "abc", "volume": 100.0}
    added = {"id": "abc", "volume": 100.0, "spread": 0.02}
    removed = {"id": "abc"}
    assert response_shape_hash(base) != response_shape_hash(added)
    assert response_shape_hash(base) != response_shape_hash(removed)


def test_response_shape_hash_independent_of_key_order() -> None:
    a = {"id": "abc", "volume": 100.0, "spread": 0.02}
    b = {"spread": 0.02, "volume": 100.0, "id": "abc"}
    assert response_shape_hash(a) == response_shape_hash(b)


def test_response_shape_hash_handles_nested_mappings() -> None:
    base = {"market": {"id": "abc", "stats": {"volume": 1.0}}}
    renamed_nested = {"market": {"id": "abc", "stats": {"vol": 1.0}}}
    assert response_shape_hash(base) != response_shape_hash(renamed_nested)


def test_compare_response_shape_clean_match() -> None:
    payload = {"id": "abc", "volume": 100.0}
    expected = response_shape_hash(payload)
    diff = compare_response_shape(payload, expected_hash=expected)
    assert diff.drifted is False
    assert diff.observed_hash == expected
    assert diff.added_keys == ()
    assert diff.removed_keys == ()


def test_compare_response_shape_flags_drift() -> None:
    pinned_payload = {"id": "abc", "volume": 100.0}
    pinned_hash = response_shape_hash(pinned_payload)
    new_payload = {"id": "abc", "volume": 100.0, "spread": 0.02}
    diff = compare_response_shape(new_payload, expected_hash=pinned_hash)
    assert diff.drifted is True
    assert diff.observed_hash != pinned_hash


def test_diff_shapes_reports_added_and_removed_paths() -> None:
    expected = {"id": "abc", "volume": 100.0, "spread": 0.02}
    observed = {"id": "abc", "volume": 100.0, "depth": 1500.0}
    diff = diff_shapes(observed, expected)
    assert diff.drifted is True
    assert "depth" in diff.added_keys
    assert "spread" in diff.removed_keys


def test_diff_shapes_clean_when_identical_keys() -> None:
    a = {"id": "abc", "volume": 100.0}
    b = {"id": "different", "volume": 999.0}
    diff = diff_shapes(a, b)
    assert diff.drifted is False
    assert diff.added_keys == ()
    assert diff.removed_keys == ()
