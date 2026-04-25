"""
Helpers for assessing market liquidity conditions.

This module contains a simple checker for determining whether a market’s order
book is considered “thin” based on the notional depth available at the best
bid and ask prices. Thin books can lead to higher slippage and may warrant
either excluding the market from certain strategies or tagging it in the UI.

Functions:

* :func:`is_thin_book` – return True if the combined depth falls below a
  configurable threshold.
"""

from __future__ import annotations

from typing import Iterable, Tuple


def is_thin_book(
    bids: Iterable[Tuple[float, float]],
    asks: Iterable[Tuple[float, float]],
    threshold: float = 1000.0,
) -> bool:
    """Determine if the top-of-book notional depth is below a threshold.

    Parameters
    ----------
    bids:
        Iterable of ``(price, quantity)`` tuples for the bid side. Only the
        best bid (highest price) is considered.
    asks:
        Iterable of ``(price, quantity)`` tuples for the ask side. Only the
        best ask (lowest price) is considered.
    threshold:
        Minimum combined notional (bid_price * bid_qty + ask_price * ask_qty)
        required to consider the book sufficiently deep.

    Returns
    -------
    bool
        True if the book depth is below ``threshold``, False otherwise.
    """
    try:
        best_bid = max(bids, key=lambda x: float(x[0]))
    except ValueError:
        best_bid = None
    try:
        best_ask = min(asks, key=lambda x: float(x[0]))
    except ValueError:
        best_ask = None
    notional = 0.0
    if best_bid is not None:
        try:
            price, qty = float(best_bid[0]), float(best_bid[1])
            notional += price * qty
        except (TypeError, ValueError):
            pass
    if best_ask is not None:
        try:
            price, qty = float(best_ask[0]), float(best_ask[1])
            notional += price * qty
        except (TypeError, ValueError):
            pass
    return notional < threshold