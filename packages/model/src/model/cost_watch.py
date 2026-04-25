"""
Simple cost aggregation helpers for third‑party APIs.

The product specification calls for monitoring and controlling API spend across
providers (§9). While the production system will collect per‑request metrics
from ingest workers and external API clients, this module offers basic
utilities to aggregate call counts and multiply them by per‑call unit prices.
These helpers facilitate quick cost introspection during development and
testing.

Functions:

* :func:`estimate_api_cost` – given a mapping of provider names to call counts
  and another mapping of provider names to per‑call unit prices, return the
  total estimated cost and a breakdown per provider.
"""

from __future__ import annotations

from typing import Dict, Tuple


def estimate_api_cost(
    call_counts: Dict[str, int] | None,
    unit_prices: Dict[str, float] | None,
) -> Tuple[float, Dict[str, float]]:
    """Estimate total cost of API calls.

    Parameters
    ----------
    call_counts:
        Mapping from provider name to the number of requests made. Missing
        providers are treated as zero calls.
    unit_prices:
        Mapping from provider name to per‑call cost (e.g. in USD). Missing
        providers default to zero cost per call.

    Returns
    -------
    Tuple[float, Dict[str, float]]
        The total estimated cost and a breakdown by provider.
    """
    total = 0.0
    breakdown: Dict[str, float] = {}
    call_counts = call_counts or {}
    unit_prices = unit_prices or {}
    for provider, count in call_counts.items():
        try:
            calls = int(count)
        except (TypeError, ValueError):
            continue
        price_per_call = float(unit_prices.get(provider, 0.0))
        cost = calls * price_per_call
        breakdown[provider] = cost
        total += cost
    return total, breakdown