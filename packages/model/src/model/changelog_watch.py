"""
Simple helper for monitoring API version changes.

The cross‑cutting requirements mention watching for upstream API changes and
pinning client versions (§9). A fully‑fledged monitor would periodically
query the Polymarket API, compare the returned version string to a stored
value, and trigger alerts when a change occurs. This stub exposes a
comparison function that can be used by such a monitor to decide whether
updates are required.

Functions:

* :func:`is_newer_version` – compare two semantic version strings and return
  ``True`` if the first is greater than the second. Uses PEP 440
  version parsing via ``packaging.version`` if available; falls back to
  lexical comparison otherwise.
"""

from __future__ import annotations

from typing import Any

try:
    # Prefer PEP 440–compliant parsing when available.
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