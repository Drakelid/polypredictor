"""Polymarket schema-drift watcher (PRD §9 cross-cutting).

Wraps the pure helpers in :mod:`model.changelog_watch` so an operator can
verify a fresh upstream payload against a pinned-on-disk shape hash. The
intended workflow:

1. Capture a known-good response sample to ``services/api/data/polymarket_pins.json``
   (the fixture file format is documented below).
2. The nightly watcher fetches a small representative response from each
   tracked endpoint, computes its shape hash, and compares against the pin.
3. Drift produces a structured record the operator can act on.

This module is read-only — the actual fetching is owned by the caller (the
ingest worker that imports this module today, the audit dashboard
tomorrow). That separation keeps the helpers unit-testable without a
network and lets the same code drive a one-shot CLI audit and a periodic
worker.

Pin file format (JSON list)::

    [
      {"endpoint": "gamma:/markets", "shape_hash": "abc...", "version": "v1"},
      {"endpoint": "gamma:/events",  "shape_hash": "def...", "version": "v1"}
    ]
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from model import compare_response_shape


@dataclass(frozen=True)
class PinnedShape:
    endpoint: str
    shape_hash: str
    version: str | None = None


@dataclass(frozen=True)
class SchemaDriftRecord:
    endpoint: str
    drifted: bool
    expected_hash: str
    observed_hash: str
    pinned_version: str | None
    added_keys: tuple[str, ...]


def load_pinned_shapes(path: str | Path) -> list[PinnedShape]:
    """Parse the operator-maintained pin file.

    Returns an empty list when the file is missing — the caller is then
    expected to surface that absence instead of silently passing.
    """
    file_path = Path(path)
    if not file_path.is_file():
        return []
    payload = json.loads(file_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        return []
    out: list[PinnedShape] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        endpoint = str(entry.get("endpoint") or "").strip()
        shape_hash = str(entry.get("shape_hash") or "").strip()
        if not endpoint or not shape_hash:
            continue
        version = entry.get("version")
        out.append(
            PinnedShape(
                endpoint=endpoint,
                shape_hash=shape_hash,
                version=str(version).strip() if isinstance(version, str) else None,
            )
        )
    return out


def evaluate_drift(
    *,
    pinned: list[PinnedShape],
    samples: dict[str, Any],
) -> list[SchemaDriftRecord]:
    """Compare each pinned endpoint against the matching sample in ``samples``.

    A pinned endpoint with no matching sample is recorded as drifted so the
    operator can't silently lose coverage by removing the fetcher. A sample
    with no matching pin is ignored (operator must add a pin to start
    tracking it).
    """
    pin_by_endpoint = {pin.endpoint: pin for pin in pinned}
    out: list[SchemaDriftRecord] = []
    for endpoint, pin in sorted(pin_by_endpoint.items()):
        if endpoint not in samples:
            out.append(
                SchemaDriftRecord(
                    endpoint=endpoint,
                    drifted=True,
                    expected_hash=pin.shape_hash,
                    observed_hash="",
                    pinned_version=pin.version,
                    added_keys=("<no_sample>",),
                )
            )
            continue
        diff = compare_response_shape(samples[endpoint], expected_hash=pin.shape_hash)
        out.append(
            SchemaDriftRecord(
                endpoint=endpoint,
                drifted=diff.drifted,
                expected_hash=diff.expected_hash,
                observed_hash=diff.observed_hash,
                pinned_version=pin.version,
                added_keys=diff.added_keys,
            )
        )
    return out


__all__ = [
    "PinnedShape",
    "SchemaDriftRecord",
    "evaluate_drift",
    "load_pinned_shapes",
]
