"""MarketsRegistry tests."""

from __future__ import annotations

import pytest
from ingest.markets_registry import MarketRef, MarketsRegistry


def _ref(cid: str, vol: float, *, active: bool = True, closed: bool = False) -> MarketRef:
    return MarketRef(
        condition_id=cid,
        token_ids=[f"{cid}-y", f"{cid}-n"],
        volume_usdc=vol,
        liquidity_usdc=0.0,
        active=active,
        closed=closed,
    )


@pytest.mark.asyncio
async def test_replace_filters_inactive_and_closed() -> None:
    reg = MarketsRegistry()
    await reg.replace(
        [
            _ref("a", 1000),
            _ref("b", 500, closed=True),
            _ref("c", 100, active=False),
        ]
    )
    all_ = await reg.all()
    assert [r.condition_id for r in all_] == ["a"]


@pytest.mark.asyncio
async def test_top_n_by_volume() -> None:
    reg = MarketsRegistry()
    await reg.replace([_ref("a", 100), _ref("b", 500), _ref("c", 250)])
    top = await reg.top_n_by_volume(2)
    assert [r.condition_id for r in top] == ["b", "c"]


@pytest.mark.asyncio
async def test_tail_by_volume() -> None:
    reg = MarketsRegistry()
    await reg.replace([_ref("a", 100), _ref("b", 500), _ref("c", 250)])
    tail = await reg.tail_by_volume(1)
    # After skipping rank-1 ("b"), we expect c then a.
    assert [r.condition_id for r in tail] == ["c", "a"]
