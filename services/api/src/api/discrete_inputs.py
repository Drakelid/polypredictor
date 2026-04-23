"""Manual/cached discrete-event baseline inputs.

Until live FedWatch / survey integrations land, the API reads a small JSON
cache file that maps event families (or specific markets) to baseline priors.
This keeps the data source explicit and auditable instead of burying it in
environment variables.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DiscreteBaselineRecord:
    fedwatch_prob: float | None = None
    consensus_point: float | None = None
    consensus_stddev: float | None = None
    source: str | None = None
    as_of: str | None = None


@dataclass(frozen=True)
class DiscreteBaselineFile:
    families: dict[str, DiscreteBaselineRecord]
    markets: list[dict[str, Any]]


def resolve_discrete_record(
    *,
    path: str | None,
    event_family: str | None,
    condition_id: str | None,
    slug: str | None,
    question: str | None,
) -> DiscreteBaselineRecord | None:
    data = load_discrete_file(path)
    if data is None:
        return None

    for market in data.markets:
        if not _market_matches(
            market,
            condition_id=condition_id,
            slug=slug,
            question=question,
            event_family=event_family,
        ):
            continue
        return _record_from_mapping(market)

    if event_family is None:
        return None
    return data.families.get(event_family)


@lru_cache(maxsize=8)
def load_discrete_file(path: str | None) -> DiscreteBaselineFile | None:
    if path is None or not path.strip():
        return None
    file_path = Path(path)
    if not file_path.exists():
        return None

    payload = json.loads(file_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return None

    families_payload = payload.get("families", {})
    markets_payload = payload.get("markets", [])

    families: dict[str, DiscreteBaselineRecord] = {}
    if isinstance(families_payload, dict):
        for family, raw in families_payload.items():
            if isinstance(raw, dict):
                families[str(family)] = _record_from_mapping(raw)

    markets: list[dict[str, Any]] = []
    if isinstance(markets_payload, list):
        for raw in markets_payload:
            if isinstance(raw, dict):
                markets.append(raw)

    return DiscreteBaselineFile(families=families, markets=markets)


def _record_from_mapping(raw: dict[str, Any]) -> DiscreteBaselineRecord:
    return DiscreteBaselineRecord(
        fedwatch_prob=_opt_float(raw.get("fedwatch_prob")),
        consensus_point=_opt_float(raw.get("consensus_point")),
        consensus_stddev=_opt_float(raw.get("consensus_stddev")),
        source=_opt_str(raw.get("source")),
        as_of=_opt_str(raw.get("as_of")),
    )


def _market_matches(
    raw: dict[str, Any],
    *,
    condition_id: str | None,
    slug: str | None,
    question: str | None,
    event_family: str | None,
) -> bool:
    match_condition_id = _opt_str(raw.get("condition_id"))
    if match_condition_id is not None and match_condition_id != condition_id:
        return False

    match_slug = _opt_str(raw.get("slug"))
    if match_slug is not None and match_slug != slug:
        return False

    match_family = _opt_str(raw.get("event_family"))
    if match_family is not None and match_family != event_family:
        return False

    question_contains = _opt_str(raw.get("question_contains"))
    if question_contains is not None:
        lowered_question = (question or "").lower()
        if question_contains.lower() not in lowered_question:
            return False

    return (
        match_condition_id is not None
        or match_slug is not None
        or match_family is not None
        or question_contains is not None
    )


def _opt_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _opt_str(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None
