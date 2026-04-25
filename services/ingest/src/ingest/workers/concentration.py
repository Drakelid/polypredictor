"""Pure functions for holder-concentration metrics (M3.2).

Inputs are already-bucketed lists of sizes per outcome. These are kept as
a standalone module (no I/O) so the dashboard can reuse them in tests and
so the poller is a thin shell around pure logic.

Metrics (PRD §6 / §9):

* **Gini coefficient** on the top-N holder sizes within an outcome. Scale is
  [0, 1]: 0 = uniform across holders, 1 = one wallet owns everything among
  those returned. Note that we only see the top-N, so this is the Gini of
  the concentrated tail — still useful as a monotone indicator.
* **Top-1 / top-5 outcome share** — absolute pct of the summed top-N owned
  by the single largest / top five holders.
* **Whale flag** — ``top1_pct > whale_threshold`` (PRD: 40%). Flagged
  markets get a badge in the UI and trigger the down-weight rule in the
  ensemble.
"""

from __future__ import annotations

from dataclasses import dataclass

WHALE_THRESHOLD_DEFAULT = 0.40


@dataclass(frozen=True)
class OutcomeConcentration:
    gini: float | None
    top1_pct: float | None
    top5_pct: float | None
    whale_flag: bool
    holders_count: int
    total_size: float


def gini_coefficient(sizes: list[float]) -> float | None:
    """Gini over a list of non-negative sizes.

    Returns ``None`` if there is not enough signal (fewer than two non-zero
    holders). Uses the Lorenz-curve formula:

        G = (sum_i (2i - n - 1) * x_i) / (n * sum(x_i))

    after sorting ascending; this is equivalent to the mean-absolute-
    difference definition for non-negative values and is O(n log n).
    """
    positive = [size for size in sizes if size > 0.0]
    if len(positive) < 2:
        return None
    ordered = sorted(positive)
    n = len(ordered)
    total = sum(ordered)
    if total <= 0.0:
        return None
    weighted = sum((2 * (idx + 1) - n - 1) * value for idx, value in enumerate(ordered))
    gini = weighted / (n * total)
    return max(0.0, min(1.0, gini))


def concentration_for_outcome(
    sizes: list[float],
    *,
    whale_threshold: float = WHALE_THRESHOLD_DEFAULT,
) -> OutcomeConcentration:
    positive = [size for size in sizes if size > 0.0]
    total = sum(positive)
    holders_count = len(positive)
    if total <= 0.0 or holders_count == 0:
        return OutcomeConcentration(
            gini=None,
            top1_pct=None,
            top5_pct=None,
            whale_flag=False,
            holders_count=holders_count,
            total_size=0.0,
        )
    descending = sorted(positive, reverse=True)
    top1 = descending[0] / total
    top5 = sum(descending[:5]) / total
    return OutcomeConcentration(
        gini=gini_coefficient(positive),
        top1_pct=top1,
        top5_pct=top5,
        whale_flag=top1 > whale_threshold,
        holders_count=holders_count,
        total_size=total,
    )
