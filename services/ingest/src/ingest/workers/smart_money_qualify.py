"""Wash-trading guards + per-market aggregation for smart-money tracking.

PRD §6 / §9 call for:

* **PnL+VOL cross-check** — a wallet must rank on *both* the PNL and VOL
  orderings before we treat it as signal. Single-ordering appearances catch
  either lucky single-trade winners (PNL only) or churn / wash-trade volume
  (VOL only).
* **Multi-market requirement** — qualifying wallets must hold positions in
  several distinct markets; farming concentrates on one market.
* **Minimum volume** — a volume floor on the leaderboard entry; since the
  public leaderboard does not expose trade counts, we use reported volume as
  the proxy the PRD calls out in "minimum trade count".

These guards run in-process so they are easy to tune and test. The resulting
``QualificationDecision`` list is written to ``smart_money_qualifications``
(one row per wallet per refresh). Only qualified wallets feed the per-market
consensus aggregation.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from polymarket_client import Position


@dataclass(frozen=True)
class QualificationThresholds:
    """Knobs for the wash-trading guards (PRD §9)."""

    min_volume: float
    min_distinct_markets: int


@dataclass(frozen=True)
class QualificationDecision:
    proxy_wallet: str
    qualified: bool
    reasons: tuple[str, ...]
    distinct_markets: int
    leaderboard_rank: int | None
    leaderboard_pnl: float | None
    leaderboard_vol: float | None
    sources: tuple[str, ...]
    appeared_in_pnl: bool
    appeared_in_vol: bool
    min_volume_ok: bool


@dataclass(frozen=True)
class PerMarketAggregate:
    """Aggregated qualified-wallet positions for a single market."""

    condition_id: str
    yes_wallets: int
    no_wallets: int
    yes_size: float
    no_size: float
    yes_size_usdc: float
    no_size_usdc: float
    yes_avg_entry: float | None
    no_avg_entry: float | None
    sample_wallets: int


def _appeared_in(sources: tuple[str, ...], ordering: str) -> bool:
    suffix = f":{ordering}"
    return any(source.endswith(suffix) for source in sources)


def qualify_wallet(
    *,
    proxy_wallet: str,
    sources: tuple[str, ...],
    leaderboard_rank: int | None,
    leaderboard_pnl: float | None,
    leaderboard_vol: float | None,
    positions: list[Position],
    thresholds: QualificationThresholds,
) -> QualificationDecision:
    """Evaluate a single wallet against the wash-trading guards.

    Rationale per guard:

    * ``appeared_in_pnl AND appeared_in_vol`` — cross-check per PRD §9.
      Neither ordering alone is trustworthy.
    * ``distinct_markets >= min_distinct_markets`` — single-market wallets
      cluster around farming/wash behavior; the signal we want is wallets
      repeatedly right across the venue.
    * ``leaderboard_vol >= min_volume`` — floors out dust accounts whose high
      rank is an artefact of low denominators.
    """

    distinct_markets = len({position.condition_id for position in positions})
    appeared_pnl = _appeared_in(sources, "PNL")
    appeared_vol = _appeared_in(sources, "VOL")
    min_volume_ok = (
        leaderboard_vol is not None and leaderboard_vol >= thresholds.min_volume
    )

    reasons: list[str] = []
    if not appeared_pnl:
        reasons.append("missing_pnl_ordering")
    if not appeared_vol:
        reasons.append("missing_vol_ordering")
    if distinct_markets < thresholds.min_distinct_markets:
        reasons.append("below_min_distinct_markets")
    if not min_volume_ok:
        reasons.append("below_min_volume")

    qualified = not reasons
    if qualified:
        reasons = ["qualified"]

    return QualificationDecision(
        proxy_wallet=proxy_wallet,
        qualified=qualified,
        reasons=tuple(reasons),
        distinct_markets=distinct_markets,
        leaderboard_rank=leaderboard_rank,
        leaderboard_pnl=leaderboard_pnl,
        leaderboard_vol=leaderboard_vol,
        sources=sources,
        appeared_in_pnl=appeared_pnl,
        appeared_in_vol=appeared_vol,
        min_volume_ok=min_volume_ok,
    )


def aggregate_per_market(
    positions_by_wallet: dict[str, list[Position]],
    qualified_wallets: set[str],
) -> list[PerMarketAggregate]:
    """Roll up qualified-wallet positions into per-market summaries.

    The per-market consensus (``(yes_usdc - no_usdc) / total_usdc``) is
    computed downstream from the USDC totals this function emits, so
    aggregation remains a pure function of the positions passed in.
    """

    yes_size: dict[str, float] = defaultdict(float)
    no_size: dict[str, float] = defaultdict(float)
    yes_usdc: dict[str, float] = defaultdict(float)
    no_usdc: dict[str, float] = defaultdict(float)
    yes_weighted_entry: dict[str, float] = defaultdict(float)
    no_weighted_entry: dict[str, float] = defaultdict(float)
    yes_wallets: dict[str, set[str]] = defaultdict(set)
    no_wallets: dict[str, set[str]] = defaultdict(set)
    all_wallets: dict[str, set[str]] = defaultdict(set)

    for wallet, positions in positions_by_wallet.items():
        if wallet not in qualified_wallets:
            continue
        for position in positions:
            size = float(position.size)
            if size <= 0.0:
                continue
            cid = position.condition_id
            usdc = float(position.current_value)
            avg_price = float(position.avg_price)
            all_wallets[cid].add(wallet)
            if position.outcome.upper() == "YES":
                yes_size[cid] += size
                yes_usdc[cid] += usdc
                yes_weighted_entry[cid] += size * avg_price
                yes_wallets[cid].add(wallet)
            else:
                no_size[cid] += size
                no_usdc[cid] += usdc
                no_weighted_entry[cid] += size * avg_price
                no_wallets[cid].add(wallet)

    aggregates: list[PerMarketAggregate] = []
    for cid in sorted(all_wallets.keys()):
        y_size = yes_size.get(cid, 0.0)
        n_size = no_size.get(cid, 0.0)
        aggregates.append(
            PerMarketAggregate(
                condition_id=cid,
                yes_wallets=len(yes_wallets.get(cid, set())),
                no_wallets=len(no_wallets.get(cid, set())),
                yes_size=y_size,
                no_size=n_size,
                yes_size_usdc=yes_usdc.get(cid, 0.0),
                no_size_usdc=no_usdc.get(cid, 0.0),
                yes_avg_entry=(
                    yes_weighted_entry[cid] / y_size if y_size > 0.0 else None
                ),
                no_avg_entry=(
                    no_weighted_entry[cid] / n_size if n_size > 0.0 else None
                ),
                sample_wallets=len(all_wallets.get(cid, set())),
            )
        )
    return aggregates
