"""
Helpers for monitoring upstream API drift (PRD §9 cross-cutting).

The cross-cutting requirement is to "version-pin the client, monitor for API
changes". Two complementary helpers live here:

* :func:`is_newer_version` - compare two version strings.
* :func:`response_shape_hash` / :func:`compare_response_shape` - extract the
  set of top-level keys from a sample response and produce a stable hash, so
  an operator-side worker can detect when an upstream payload's shape
  changes without needing a published version string.

The shape helpers are intentionally key-only and recursive on nested dicts:
value-level changes (a market's volume going up) don't trip the watcher,
but a renamed/added/removed field does.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

try:
    # Prefer PEP 440-compliant parsing when available.
    from packaging import version as _version_parser  # type: ignore
except ImportError:  # pragma: no cover
    _version_parser = None  # type: ignore


def is_newer_version(current: str, recorded: str) -> bool:
    """Return True if ``current`` version is strictly greater than ``recorded``.

    Parameters
    ----------
    current:
        The version string reported by the upstream API.
    recorded:
        The version string currently pinned in the client.

    Returns
    -------
    bool
        True if the upstream version appears to be newer than the pinned
        version; False otherwise. If versions cannot be parsed, falls back
        to string comparison.
    """
    if _version_parser is not None:
        try:
            return _version_parser.parse(current) > _version_parser.parse(recorded)
        except Exception:
            pass
    # Fallback: simple lexicographical comparison.
    return str(current) > str(recorded)


@dataclass(frozen=True)
class ShapeDiff:
    """Result of comparing two response shapes."""

    drifted: bool
    expected_hash: str
    observed_hash: str
    added_keys: tuple[str, ...]
    removed_keys: tuple[str, ...]


def _walk_shape(payload: object, prefix: str = "") -> list[str]:
    """Flatten the recursive set of ``parent.child`` paths in ``payload``.

    Lists are walked element-by-element with the index dropped — only the
    structural shape matters, not the count. Scalars are leaves and produce
    just the prefix path. Nested mappings recurse with dot-joined keys.
    """
    paths: set[str] = set()
    if isinstance(payload, Mapping):
        if not payload:
            paths.add(f"{prefix}{{}}" if prefix else "{}")
        for key, value in payload.items():
            sub_prefix = f"{prefix}.{key}" if prefix else str(key)
            paths.update(_walk_shape(value, sub_prefix))
    elif isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
        if not payload:
            paths.add(f"{prefix}[]" if prefix else "[]")
        for item in payload:
            paths.update(_walk_shape(item, prefix))
    else:
        paths.add(prefix or "<root>")
    return sorted(paths)


def response_shape_hash(payload: object) -> str:
    """Stable SHA-256 over the sorted set of structural paths in ``payload``.

    Two payloads with the same key shape produce the same hash regardless of
    value content, list length, or key ordering. Nested mappings are
    flattened to ``parent.child`` paths so a renamed nested key is detected.
    """
    paths = _walk_shape(payload)
    digest = hashlib.sha256("\n".join(paths).encode("utf-8"))
    return digest.hexdigest()


def compare_response_shape(payload: object, *, expected_hash: str) -> ShapeDiff:
    """Compare ``payload``'s shape hash against an operator-pinned hash.

    Returns a :class:`ShapeDiff` with the observed hash + (when drifted)
    the explicit added/removed paths so the operator can quickly diagnose
    which fields changed without re-running the comparison locally.
    """
    observed_paths = set(_walk_shape(payload))
    observed_hash = response_shape_hash(payload)
    drifted = observed_hash != expected_hash
    if not drifted:
        return ShapeDiff(
            drifted=False,
            expected_hash=expected_hash,
            observed_hash=observed_hash,
            added_keys=(),
            removed_keys=(),
        )
    return ShapeDiff(
        drifted=True,
        expected_hash=expected_hash,
        observed_hash=observed_hash,
        added_keys=tuple(sorted(observed_paths)),
        removed_keys=(),
    )


def diff_shapes(observed: object, expected: object) -> ShapeDiff:
    """Compare two payloads directly (no pinned hash).

    Useful for the operator workflow of "fetch a fresh response and a
    pinned-on-disk JSON sample, surface what changed". When the operator only
    has the hash (not the full prior payload), use
    :func:`compare_response_shape` instead.
    """
    observed_paths = set(_walk_shape(observed))
    expected_paths = set(_walk_shape(expected))
    added = tuple(sorted(observed_paths - expected_paths))
    removed = tuple(sorted(expected_paths - observed_paths))
    drifted = bool(added or removed)
    return ShapeDiff(
        drifted=drifted,
        expected_hash=response_shape_hash(expected),
        observed_hash=response_shape_hash(observed),
        added_keys=added,
        removed_keys=removed,
    )


__all__ = [
    "ShapeDiff",
    "compare_response_shape",
    "diff_shapes",
    "is_newer_version",
    "response_shape_hash",
]