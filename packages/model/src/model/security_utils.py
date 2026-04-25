"""
Security helpers for secret rotation policies.

The product requirements stipulate periodic rotation of encrypted secrets and
API keys. While secret management is typically handled by the infrastructure
layer (e.g. AWS Secrets Manager, HashiCorp Vault), this module includes a
simple date-based helper to determine when a secret should be rotated based
on the last rotation timestamp and a configured rotation interval.

Functions:

* :func:`needs_rotation` - check if the interval since last rotation exceeds
  a threshold.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def needs_rotation(
    last_rotation: datetime | None,
    rotation_interval_days: int = 90,
) -> bool:
    """Return True if more than ``rotation_interval_days`` have elapsed.

    Parameters
    ----------
    last_rotation:
        Datetime of the last secret rotation. If None, always returns True.
        Naive datetimes are interpreted as UTC.
    rotation_interval_days:
        Number of days after which a rotation is recommended.

    Returns
    -------
    bool
        True if the time elapsed since ``last_rotation`` exceeds
        ``rotation_interval_days``; false otherwise.
    """
    if last_rotation is None:
        return True
    if last_rotation.tzinfo is None:
        last_rotation = last_rotation.replace(tzinfo=UTC)
    return datetime.now(UTC) - last_rotation > timedelta(days=rotation_interval_days)