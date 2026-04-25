"""Sibling-market ordering and no-arb helpers for M3.4.

The PRD calls out three sibling classes:

* same threshold, different date
* same date, different threshold
* mutually-exclusive multi-outcome legs

This module keeps the ordering / detection rules pure so ingestion can emit
``arb`` signal events and the API can derive sibling-implied priors from the
same logic.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from .types import MarketType


@dataclass(frozen=True)
class SiblingQuote:
    mid: float | None
    best_bid: float | None
    best_ask: float | None
    bid_size: float | None = None
    ask_size: float | None = None


@dataclass(frozen=True)
class SiblingMarket:
    condition_id: str
    question: str
    event_id: str | None
    market_type: MarketType
    asset: str | None = None
    direction: str | None = None
    strike: float | None = None
    resolution_date: datetime | None = None
    quote: SiblingQuote = SiblingQuote(None, None, None)


@dataclass(frozen=True)
class OrderedThresholdPair:
    relation_type: str
    looser: SiblingMarket
    stricter: SiblingMarket


@dataclass(frozen=True)
class MultiOutcomeGroup:
    event_id: str
    markets: tuple[SiblingMarket, ...]


@dataclass(frozen=True)
class ArbViolation:
    event_id: str
    condition_id: str
    relation_type: str
    locked_profit_per_share: float
    implied_size: float | None
    implied_profit_usdc: float | None
    severity: float
    direction: str
    payload: dict[str, object]
    event_time: datetime


@dataclass(frozen=True)
class SiblingPrior:
    lower_bound: float | None
    upper_bound: float | None
    implied_prior: float | None
    support_conditions: tuple[str, ...]


def build_threshold_pairs(markets: list[SiblingMarket]) -> list[OrderedThresholdPair]:
    """Build monotonic threshold/date sibling constraints.

    Rules:
    * same asset + direction + resolution date: looser threshold dominates
      stricter threshold.
    * same asset + direction + strike: later expiry dominates earlier expiry.
    """

    thresholds = [
        market
        for market in markets
        if market.market_type is MarketType.THRESHOLD
        and market.asset is not None
        and market.direction in {"above", "below"}
        and market.strike is not None
        and market.resolution_date is not None
    ]
    out: list[OrderedThresholdPair] = []
    seen: set[tuple[str, str, str]] = set()
    for idx, left in enumerate(thresholds):
        for right in thresholds[idx + 1 :]:
            if left.asset != right.asset or left.direction != right.direction:
                continue
            pair: OrderedThresholdPair | None = None
            if left.resolution_date == right.resolution_date and left.strike != right.strike:
                if left.direction == "above":
                    looser, stricter = sorted(
                        (left, right),
                        key=lambda market: float(market.strike or 0.0),
                    )
                else:
                    stricter, looser = sorted(
                        (left, right),
                        key=lambda market: float(market.strike or 0.0),
                    )
                pair = OrderedThresholdPair(
                    relation_type="same_date_threshold",
                    looser=looser,
                    stricter=stricter,
                )
            elif left.strike == right.strike and left.resolution_date != right.resolution_date:
                earlier, later = sorted(
                    (left, right),
                    key=lambda market: market.resolution_date or datetime.min,
                )
                pair = OrderedThresholdPair(
                    relation_type="same_threshold_date",
                    looser=later,
                    stricter=earlier,
                )
            if pair is None:
                continue
            key = (
                pair.relation_type,
                pair.looser.condition_id,
                pair.stricter.condition_id,
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(pair)
    out.sort(
        key=lambda pair: (
            pair.relation_type,
            pair.looser.condition_id,
            pair.stricter.condition_id,
        )
    )
    return out


def build_multi_outcome_groups(markets: list[SiblingMarket]) -> list[MultiOutcomeGroup]:
    grouped: dict[str, list[SiblingMarket]] = {}
    for market in markets:
        if market.market_type is not MarketType.MULTI_OUTCOME:
            continue
        if not market.event_id:
            continue
        grouped.setdefault(market.event_id, []).append(market)
    out = [
        MultiOutcomeGroup(
            event_id=event_id,
            markets=tuple(sorted(group, key=lambda market: market.condition_id)),
        )
        for event_id, group in grouped.items()
        if len(group) >= 2
    ]
    out.sort(key=lambda group: group.event_id)
    return out


def sibling_prior_for_market(
    target: SiblingMarket,
    threshold_pairs: list[OrderedThresholdPair],
) -> SiblingPrior | None:
    lower_bound: float | None = None
    upper_bound: float | None = None
    support: set[str] = set()

    for pair in threshold_pairs:
        if pair.looser.condition_id == target.condition_id:
            candidate = pair.stricter.quote.mid
            if candidate is not None:
                lower_bound = candidate if lower_bound is None else max(lower_bound, candidate)
                support.add(pair.stricter.condition_id)
        elif pair.stricter.condition_id == target.condition_id:
            candidate = pair.looser.quote.mid
            if candidate is not None:
                upper_bound = candidate if upper_bound is None else min(upper_bound, candidate)
                support.add(pair.looser.condition_id)

    if lower_bound is None and upper_bound is None:
        return None
    if lower_bound is not None and upper_bound is not None:
        lo = min(lower_bound, upper_bound)
        hi = max(lower_bound, upper_bound)
        implied = 0.5 * (lo + hi)
        return SiblingPrior(
            lower_bound=lower_bound,
            upper_bound=upper_bound,
            implied_prior=implied,
            support_conditions=tuple(sorted(support)),
        )
    implied = lower_bound if lower_bound is not None else upper_bound
    return SiblingPrior(
        lower_bound=lower_bound,
        upper_bound=upper_bound,
        implied_prior=implied,
        support_conditions=tuple(sorted(support)),
    )


def detect_threshold_arbs(
    threshold_pairs: list[OrderedThresholdPair],
    *,
    event_time: datetime,
    taker_fee_bps: float = 0.0,
) -> list[ArbViolation]:
    out: list[ArbViolation] = []
    for pair in threshold_pairs:
        looser_ask = pair.looser.quote.best_ask
        stricter_bid = pair.stricter.quote.best_bid
        if looser_ask is None or stricter_bid is None:
            continue
        fee = _fee_cost(
            notionals=(looser_ask, stricter_bid),
            taker_fee_bps=taker_fee_bps,
        )
        locked_profit = stricter_bid - looser_ask - fee
        if locked_profit <= 0.0:
            continue
        violation_gap = (
            (pair.stricter.quote.mid or stricter_bid) - (pair.looser.quote.mid or looser_ask)
        )
        condition_id = pair.stricter.condition_id
        out.append(
            ArbViolation(
                event_id=_arb_event_id(
                    relation_type=pair.relation_type,
                    condition_ids=(pair.looser.condition_id, pair.stricter.condition_id),
                    event_time=event_time,
                ),
                condition_id=condition_id,
                relation_type=pair.relation_type,
                locked_profit_per_share=locked_profit,
                implied_size=_pair_implied_size(pair),
                implied_profit_usdc=_pair_implied_size(pair) * locked_profit
                if _pair_implied_size(pair) is not None
                else None,
                severity=locked_profit * 10_000.0,
                direction=(pair.stricter.direction or "neutral"),
                payload={
                    "looser_condition_id": pair.looser.condition_id,
                    "stricter_condition_id": pair.stricter.condition_id,
                    "looser_question": pair.looser.question,
                    "stricter_question": pair.stricter.question,
                    "looser_ask": looser_ask,
                    "stricter_bid": stricter_bid,
                    "looser_ask_size": pair.looser.quote.ask_size,
                    "stricter_bid_size": pair.stricter.quote.bid_size,
                    "violation_gap": violation_gap,
                    "fee_cost": fee,
                },
                event_time=event_time,
            )
        )
    out.sort(key=lambda item: item.locked_profit_per_share, reverse=True)
    return out


def detect_multi_outcome_arbs(
    groups: list[MultiOutcomeGroup],
    *,
    event_time: datetime,
    taker_fee_bps: float = 0.0,
) -> list[ArbViolation]:
    out: list[ArbViolation] = []
    for group in groups:
        asks = [market.quote.best_ask for market in group.markets]
        if any(ask is None for ask in asks):
            continue
        ask_values = [float(ask) for ask in asks if ask is not None]
        fee = _fee_cost(notionals=tuple(ask_values), taker_fee_bps=taker_fee_bps)
        basket_cost = sum(ask_values) + fee
        locked_profit = 1.0 - basket_cost
        if locked_profit <= 0.0:
            continue
        anchor = min(group.markets, key=lambda market: market.quote.best_ask or 1.0)
        implied_size = _basket_implied_size(group)
        out.append(
            ArbViolation(
                event_id=_arb_event_id(
                    relation_type="multi_outcome_basket",
                    condition_ids=tuple(market.condition_id for market in group.markets),
                    event_time=event_time,
                ),
                condition_id=anchor.condition_id,
                relation_type="multi_outcome_basket",
                locked_profit_per_share=locked_profit,
                implied_size=implied_size,
                implied_profit_usdc=implied_size * locked_profit if implied_size is not None else None,
                severity=locked_profit * 10_000.0,
                direction="neutral",
                payload={
                    "event_id": group.event_id,
                    "condition_ids": [market.condition_id for market in group.markets],
                    "questions": [market.question for market in group.markets],
                    "ask_sum": sum(ask_values),
                    "ask_sizes": [market.quote.ask_size for market in group.markets],
                    "fee_cost": fee,
                },
                event_time=event_time,
            )
        )
    out.sort(key=lambda item: item.locked_profit_per_share, reverse=True)
    return out


def _arb_event_id(
    *,
    relation_type: str,
    condition_ids: tuple[str, ...],
    event_time: datetime,
) -> str:
    seed = "|".join((relation_type, *sorted(condition_ids), event_time.isoformat()))
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


def _fee_cost(*, notionals: tuple[float, ...], taker_fee_bps: float) -> float:
    if taker_fee_bps <= 0.0:
        return 0.0
    return sum(notionals) * (taker_fee_bps / 10_000.0)


def _pair_implied_size(pair: OrderedThresholdPair) -> float | None:
    sizes = [
        value
        for value in (pair.looser.quote.ask_size, pair.stricter.quote.bid_size)
        if value is not None and value > 0.0
    ]
    return min(sizes) if len(sizes) == 2 else None


def _basket_implied_size(group: MultiOutcomeGroup) -> float | None:
    sizes = [
        float(market.quote.ask_size)
        for market in group.markets
        if market.quote.ask_size is not None and market.quote.ask_size > 0.0
    ]
    return min(sizes) if len(sizes) == len(group.markets) else None
