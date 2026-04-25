"""
Simple paper‑trading toggle.

Some users may wish to log calls without staking real money. A global
paper‑trading mode allows the system to treat all trades as simulation. This
module exposes functions to set and query a paper‑trading flag. In a real
system this would be tied to user profiles and persisted; here it is a
process‑wide variable for demonstration purposes.
"""

from __future__ import annotations


_paper_trading_enabled: bool = False


def enable_paper_trading(enable: bool) -> None:
    """Enable or disable paper trading globally.

    Parameters
    ----------
    enable:
        True to enable paper trading; False to disable it.
    """
    global _paper_trading_enabled
    _paper_trading_enabled = bool(enable)


def is_paper_trading_enabled() -> bool:
    """Return whether paper trading is currently enabled."""
    return _paper_trading_enabled