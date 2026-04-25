"""
Privacy helpers for auditing data flows.

To ensure that sensitive user data does not leak outside of the user’s
account, the system must audit data structures and logs to verify that only
allowed fields are present. This module provides simple validation helpers
that operate on in-memory data structures. In production, similar logic
would be integrated into database access layers and logging middleware.

Functions:

* :func:`records_contain_only_allowed_fields` – verify that each record’s
  keys are a subset of an allowed set.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Set


def records_contain_only_allowed_fields(
    records: Iterable[Mapping[str, object]], allowed_fields: Set[str]
) -> bool:
    """Return True if each record uses only keys from ``allowed_fields``.

    Parameters
    ----------
    records:
        Iterable of dictionaries representing user data or API payloads.
    allowed_fields:
        Set of keys that are permissible in the records.

    Returns
    -------
    bool
        True if every record’s key set is a subset of ``allowed_fields``; false
        otherwise. Records that are not mappings are ignored.
    """
    for record in records:
        if not isinstance(record, Mapping):  # skip non-dict entries
            continue
        if not set(record.keys()).issubset(allowed_fields):
            return False
    return True