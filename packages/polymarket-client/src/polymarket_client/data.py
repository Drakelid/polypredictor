"""Data API sub-client — positions, leaderboard, holders, user earnings.

Base URL: ``https://data-api.polymarket.com``

All endpoints here are **public by address** — no user credentials needed to
track leaderboard wallets (PRD §5.2.1).
"""

from __future__ import annotations

from typing import Any

from .cache import TTLCache
from .http import HealthCallback, HttpTransport
from .models import HolderEntry, LeaderboardEntry, Position, UserEarnings
from .rate_limiter import DATA_DEFAULT, TokenBucket

# Per-endpoint cache TTLs (PRD §7).
TTL_POSITIONS = 60.0
TTL_LEADERBOARD = 3600.0
TTL_HOLDERS = 300.0
TTL_EARNINGS = 60.0


class DataClient:
    def __init__(
        self,
        *,
        base_url: str = "https://data-api.polymarket.com",
        cache: TTLCache[Any] | None = None,
        health_cb: HealthCallback | None = None,
    ) -> None:
        default = TokenBucket(DATA_DEFAULT, name="data:default")
        self.transport = HttpTransport(
            base_url=base_url,
            buckets={"default": default},
            default_bucket=default,
            cache=cache or TTLCache[Any](),
            default_ttl_s=60.0,
            health_cb=health_cb,
        )

    async def aclose(self) -> None:
        await self.transport.aclose()

    async def __aenter__(self) -> DataClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def positions(
        self,
        user: str,
        *,
        limit: int = 500,
        redeemable: bool | None = None,
    ) -> list[Position]:
        params: dict[str, Any] = {"user": user, "limit": limit}
        if redeemable is not None:
            params["redeemable"] = str(redeemable).lower()
        raw = await self.transport.get_json(
            "/positions",
            endpoint_class="default",
            params=params,
            ttl_s=TTL_POSITIONS,
        )
        if not isinstance(raw, list):
            return []
        out: list[Position] = []
        for p in raw:
            try:
                out.append(Position.model_validate(p))
            except Exception:
                continue
        return out

    async def leaderboard(
        self,
        *,
        category: str = "CRYPTO",
        time_period: str = "MONTH",
        order_by: str = "PNL",
        limit: int = 500,
    ) -> list[LeaderboardEntry]:
        """``GET /v1/leaderboard``. PRD §9 mitigation: caller should cross-check
        entries between ``orderBy=PNL`` and ``orderBy=VOL`` to filter wash trading.
        """
        raw = await self.transport.get_json(
            "/v1/leaderboard",
            endpoint_class="default",
            params={
                "category": category,
                "timePeriod": time_period,
                "orderBy": order_by,
                "limit": limit,
            },
            ttl_s=TTL_LEADERBOARD,
        )
        rows = raw if isinstance(raw, list) else raw.get("items", []) if isinstance(raw, dict) else []
        out: list[LeaderboardEntry] = []
        for i, r in enumerate(rows):
            try:
                entry = LeaderboardEntry.model_validate({**r, "rank": r.get("rank", i + 1)})
                out.append(entry)
            except Exception:
                continue
        return out

    async def holders(
        self, condition_ids: list[str] | str
    ) -> dict[str, list[HolderEntry]]:
        """``GET /holders?market=...``. Returns a ``condition_id -> [holders]`` map."""
        if isinstance(condition_ids, str):
            condition_ids = [condition_ids]
        if not condition_ids:
            return {}
        raw = await self.transport.get_json(
            "/holders",
            endpoint_class="default",
            params={"market": ",".join(condition_ids)},
            ttl_s=TTL_HOLDERS,
        )
        if not isinstance(raw, (list, dict)):
            return {}
        out: dict[str, list[HolderEntry]] = {}
        if isinstance(raw, list):
            # Flat list form — each entry carries a market/conditionId field.
            for entry in raw:
                cid = entry.get("market") or entry.get("conditionId") or ""
                try:
                    holder = HolderEntry.model_validate(entry)
                except Exception:
                    continue
                out.setdefault(cid, []).append(holder)
        else:
            for cid, holders in raw.items():
                out[cid] = []
                for h in holders or []:
                    try:
                        out[cid].append(HolderEntry.model_validate(h))
                    except Exception:
                        continue
        return out

    async def user_trades(self, user: str, *, limit: int = 100) -> list[dict[str, Any]]:
        raw = await self.transport.get_json(
            "/trades",
            endpoint_class="default",
            params={"user": user, "limit": limit},
            ttl_s=10.0,
        )
        return list(raw) if isinstance(raw, list) else []

    async def user_earnings(self, address: str) -> UserEarnings:
        raw = await self.transport.get_json(
            f"/users/{address}/earnings",
            endpoint_class="default",
            ttl_s=TTL_EARNINGS,
        )
        payload = raw if isinstance(raw, dict) else {}
        total = float(payload.get("totalEarnings", 0.0))
        return UserEarnings(proxyWallet=address, totalEarnings=total, payload=payload)
