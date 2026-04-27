from __future__ import annotations

import json
from pathlib import Path

import pytest
from ingest.kol_loader import kol_categories_for_author, load_kol_index


def _write_kol_file(tmp_path: Path, payload: object) -> Path:
    file_path = tmp_path / "kol_lists.json"
    file_path.write_text(json.dumps(payload), encoding="utf-8")
    return file_path


def test_load_kol_index_returns_empty_when_path_unset() -> None:
    assert load_kol_index(None) == {}
    assert load_kol_index("") == {}


def test_load_kol_index_returns_empty_when_file_missing(tmp_path: Path) -> None:
    assert load_kol_index(str(tmp_path / "absent.json")) == {}


def test_load_kol_index_returns_empty_for_invalid_json(tmp_path: Path) -> None:
    file_path = tmp_path / "bad.json"
    file_path.write_text("{ not-json", encoding="utf-8")
    assert load_kol_index(str(file_path)) == {}


def test_load_kol_index_returns_empty_when_categories_missing(tmp_path: Path) -> None:
    file_path = _write_kol_file(tmp_path, {"_doc": "no categories key"})
    assert load_kol_index(str(file_path)) == {}


def test_load_kol_index_lowercases_authors_and_sorts_categories(tmp_path: Path) -> None:
    file_path = _write_kol_file(
        tmp_path,
        {
            "categories": {
                "Crypto": ["Alice", "BOB"],
                "Macro": ["alice", "carol"],
            }
        },
    )

    index = load_kol_index(str(file_path))

    assert index == {
        "alice": ["Crypto", "Macro"],
        "bob": ["Crypto"],
        "carol": ["Macro"],
    }


def test_load_kol_index_skips_blank_authors_and_categories(tmp_path: Path) -> None:
    file_path = _write_kol_file(
        tmp_path,
        {
            "categories": {
                "  ": ["alice"],
                "valid": ["", "  ", "alice", 123, None],
            }
        },
    )

    index = load_kol_index(str(file_path))

    assert index == {"alice": ["valid"]}


def test_load_kol_index_ignores_non_list_author_payloads(tmp_path: Path) -> None:
    file_path = _write_kol_file(
        tmp_path,
        {"categories": {"crypto": "alice", "macro": ["bob"]}},
    )

    index = load_kol_index(str(file_path))

    assert index == {"bob": ["macro"]}


def test_kol_categories_for_author_returns_empty_when_no_match() -> None:
    index = {"alice": ["crypto"]}
    assert kol_categories_for_author("bob", index) == []


def test_kol_categories_for_author_is_case_insensitive() -> None:
    index = {"alice": ["crypto", "macro"]}
    assert kol_categories_for_author("ALICE", index) == ["crypto", "macro"]
    assert kol_categories_for_author("  alice  ", index) == ["crypto", "macro"]


def test_kol_categories_for_author_handles_none_inputs() -> None:
    assert kol_categories_for_author(None, {"alice": ["crypto"]}) == []
    assert kol_categories_for_author("alice", None) == []


@pytest.mark.parametrize("path", [None, ""])
def test_load_kol_index_handles_falsy_path(path: str | None) -> None:
    assert load_kol_index(path) == {}
