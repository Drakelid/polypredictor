"""
Helpers for simple “smart money” signals.

The PRD proposes incorporating features based on the behavior of top traders
("smart money") into the model. A complete implementation would maintain
leaderboard wallets, compute their positions and trades in real time, and
evaluate the predictive power of their flows. In lieu of that infrastructure,
this module offers minimal aggregations that callers can use to simulate
smart‑money signals when backtesting.

Functions:

* :func:`net_flow` – compute the net directional flow from a list of trades.
  Trades are represented as ``(wallet_id, amount)`` pairs where positive
  amounts indicate buying YES and negative amounts indicate selling YES /
  buying NO. The net flow is simply the sum of all amounts.

* :func:`weighted_flow` – compute a weighted net flow given per‑wallet weights.
  Each trade is multiplied by the wallet’s weight before summing. Unknown
  wallets default to weight 1.0.

These signals do not incorporate price information or market depth. They are
provided solely as placeholders to enable unit tests and simple feature
experiments pending a proper implementation.
"""

from __future__ import annotations

from typing import Dict, Iterable, Tuple


def net_flow(trades: Iterable[Tuple[str, float]]) -> float:
    """Return the net directional flow from a collection of trades.

    Each trade is a tuple ``(wallet_id, amount)`` where a positive
    ``amount`` denotes net buying of YES shares and a negative ``amount``
    denotes net buying of NO shares (i.e. selling YES). The net flow is
    computed as the sum of all amounts. A positive result indicates net
    bullish flow; negative indicates bearish.

    Parameters
    ----------
    trades:
        Iterable of ``(wallet_id, amount)`` tuples.

    Returns
    -------
    float
        The sum of amounts across all trades.
    """
    total = 0.0
    for _, amount in trades:
        try:
            total += float(amount)
        except (TypeError, ValueError):
            continue
    return total


def weighted_flow(
    trades: Iterable[Tuple[str, float]],
    wallet_weights: Dict[str, float] | None = None,
) -> float:
    """Compute a weighted net flow given per‑wallet weights.

    Parameters
    ----------
    trades:
        Iterable of ``(wallet_id, amount)`` tuples.
    wallet_weights:
        Optional mapping from wallet identifiers to weights reflecting how
        predictive or influential each trader is. Unknown wallets default to
        weight 1.0.

    Returns
    -------
    float
        Weighted sum of ``amount * weight`` for each trade.
    """
    if wallet_weights is None:
        # Unweighted flow equals simple net flow.
        return net_flow(trades)
    total = 0.0
    for wallet_id, amount in trades:
        try:
            weight = float(wallet_weights.get(wallet_id, 1.0))
            total += float(amount) * weight
        except (TypeError, ValueError):
            continue
    return total