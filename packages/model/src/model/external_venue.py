"""External-venue cross-check helpers for M3.4.

This module provides two lightweight views of "what outside venues imply":

* Deribit options-implied terminal probability for a threshold market.
* Binance perpetual-basis-implied probability, using the perp mark/index
  premium as a directional forward anchor and reusing the same volatility term
  structure as the options path.

The perp conversion is intentionally heuristic. A perpetual mark is not a true
    expiry-matched forward price, but the premium is still a useful conviction
    proxy. We treat ``mark / index`` as a small drift adjustment to the spot
    anchor, then run the standard terminal threshold probability against that
    shifted anchor.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import median

import httpx

from .baselines import BaselineInputs
from .baselines.threshold import threshold_baseline
from .classifier import MarketFeatures
from .deribit import DeribitIV


@dataclass(frozen=True)
class BinancePerpSnapshot:
    symbol: str
    index_price: float
    mark_price: float
    funding_rate: float | None = None


@dataclass(frozen=True)
class CoinbaseIntxPerpSnapshot:
    instrument: str
    index_price: float
    mark_price: float
    funding_rate: float | None = None
    event_time: datetime | None = None


@dataclass(frozen=True)
class SpotTickerSnapshot:
    venue: str
    symbol: str
    price: float
    bid: float | None = None
    ask: float | None = None
    last_updated_at: datetime | None = None


@dataclass(frozen=True)
class SpotValidationResult:
    reference_price: float | None
    accepted_quotes: list[SpotTickerSnapshot]
    rejected_quotes: list[SpotTickerSnapshot]
    max_divergence_bps: float
    is_valid: bool


class BinancePerpClient:
    def __init__(
        self,
        *,
        base_url: str = "https://fapi.binance.com",
        timeout_s: float = 10.0,
        cache_ttl_s: float = 10.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self._base, timeout=timeout_s)
        self._cache_ttl_s = cache_ttl_s
        self._cache: dict[str, tuple[float, BinancePerpSnapshot | None]] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> BinancePerpClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def premium_index(self, *, symbol: str) -> BinancePerpSnapshot | None:
        symbol = symbol.upper()
        now = time.monotonic()
        cached = self._cache.get(symbol)
        if cached is not None and cached[0] > now:
            return cached[1]

        response = await self._client.get("/fapi/v1/premiumIndex", params={"symbol": symbol})
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            self._cache[symbol] = (now + self._cache_ttl_s, None)
            return None
        try:
            snapshot = BinancePerpSnapshot(
                symbol=str(payload.get("symbol") or symbol),
                index_price=float(payload["indexPrice"]),
                mark_price=float(payload["markPrice"]),
                funding_rate=(
                    float(payload["lastFundingRate"])
                    if payload.get("lastFundingRate") is not None
                    else None
                ),
            )
        except (KeyError, TypeError, ValueError):
            snapshot = None
        self._cache[symbol] = (now + self._cache_ttl_s, snapshot)
        return snapshot


class CoinbaseIntxPerpClient:
    def __init__(
        self,
        *,
        base_url: str = "https://api.international.coinbase.com",
        timeout_s: float = 10.0,
        cache_ttl_s: float = 10.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self._base, timeout=timeout_s)
        self._cache_ttl_s = cache_ttl_s
        self._cache: dict[str, tuple[float, CoinbaseIntxPerpSnapshot | None]] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> CoinbaseIntxPerpClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def snapshot(self, *, instrument: str) -> CoinbaseIntxPerpSnapshot | None:
        instrument = instrument.upper()
        now = time.monotonic()
        cached = self._cache.get(instrument)
        if cached is not None and cached[0] > now:
            return cached[1]

        quote_response = await self._client.get(f"/api/v1/instruments/{instrument}/quote")
        quote_response.raise_for_status()
        quote_payload = quote_response.json()
        if not isinstance(quote_payload, dict):
            self._cache[instrument] = (now + self._cache_ttl_s, None)
            return None

        funding_response = await self._client.get(
            f"/api/v1/instruments/{instrument}/funding"
        )
        funding_response.raise_for_status()
        funding_payload = funding_response.json()
        if not isinstance(funding_payload, dict):
            self._cache[instrument] = (now + self._cache_ttl_s, None)
            return None
        try:
            snapshot = CoinbaseIntxPerpSnapshot(
                instrument=instrument,
                index_price=float(quote_payload["index_price"]),
                mark_price=float(quote_payload.get("mark_price") or funding_payload["mark_price"]),
                funding_rate=(
                    float(funding_payload["funding_rate"])
                    if funding_payload.get("funding_rate") is not None
                    else (
                        float(quote_payload["predicted_funding"])
                        if quote_payload.get("predicted_funding") is not None
                        else None
                    )
                ),
                event_time=(
                    _parse_iso_datetime(funding_payload.get("event_time"))
                    or _parse_iso_datetime(quote_payload.get("timestamp"))
                ),
            )
        except (KeyError, TypeError, ValueError):
            snapshot = None
        self._cache[instrument] = (now + self._cache_ttl_s, snapshot)
        return snapshot


class BinanceSpotClient:
    def __init__(
        self,
        *,
        base_url: str = "https://api.binance.com",
        timeout_s: float = 10.0,
        cache_ttl_s: float = 10.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self._base, timeout=timeout_s)
        self._cache_ttl_s = cache_ttl_s
        self._cache: dict[str, tuple[float, SpotTickerSnapshot | None]] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> BinanceSpotClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def book_ticker(self, *, symbol: str) -> SpotTickerSnapshot | None:
        symbol = symbol.upper()
        now = time.monotonic()
        cached = self._cache.get(symbol)
        if cached is not None and cached[0] > now:
            return cached[1]

        response = await self._client.get("/api/v3/ticker/bookTicker", params={"symbol": symbol})
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            self._cache[symbol] = (now + self._cache_ttl_s, None)
            return None
        try:
            bid = float(payload["bidPrice"])
            ask = float(payload["askPrice"])
        except (KeyError, TypeError, ValueError):
            snapshot = None
        else:
            snapshot = SpotTickerSnapshot(
                venue="binance",
                symbol=str(payload.get("symbol") or symbol),
                price=(bid + ask) / 2.0,
                bid=bid,
                ask=ask,
            )
        self._cache[symbol] = (now + self._cache_ttl_s, snapshot)
        return snapshot


@dataclass(frozen=True)
class BinanceDailyKline:
    symbol: str
    open_time: datetime
    close_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class BinanceKlinesClient:
    """Daily-bar fetch for the M6.2 regime tagger.

    Public Binance ``/api/v3/klines`` endpoint — no API key required. The
    response is a list-of-lists; we shape it into typed
    :class:`BinanceDailyKline` rows so downstream callers don't have to
    reach into magic indices.
    """

    def __init__(
        self,
        *,
        base_url: str = "https://api.binance.com",
        timeout_s: float = 10.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self._base, timeout=timeout_s)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> BinanceKlinesClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def daily_klines(
        self,
        *,
        symbol: str,
        days: int = 30,
    ) -> list[BinanceDailyKline]:
        symbol = symbol.upper()
        days = max(2, min(days, 1000))
        response = await self._client.get(
            "/api/v3/klines",
            params={"symbol": symbol, "interval": "1d", "limit": days},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            return []
        out: list[BinanceDailyKline] = []
        for row in payload:
            if not isinstance(row, list) or len(row) < 7:
                continue
            try:
                open_time_ms = int(row[0])
                close_time_ms = int(row[6])
                kline = BinanceDailyKline(
                    symbol=symbol,
                    open_time=datetime.fromtimestamp(open_time_ms / 1000.0, tz=UTC),
                    close_time=datetime.fromtimestamp(close_time_ms / 1000.0, tz=UTC),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                )
            except (TypeError, ValueError):
                continue
            out.append(kline)
        return out


class CoinbaseSpotClient:
    def __init__(
        self,
        *,
        base_url: str = "https://api.exchange.coinbase.com",
        timeout_s: float = 10.0,
        cache_ttl_s: float = 10.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self._base, timeout=timeout_s)
        self._cache_ttl_s = cache_ttl_s
        self._cache: dict[str, tuple[float, SpotTickerSnapshot | None]] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> CoinbaseSpotClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def product_ticker(self, *, product_id: str) -> SpotTickerSnapshot | None:
        product_id = product_id.upper()
        now = time.monotonic()
        cached = self._cache.get(product_id)
        if cached is not None and cached[0] > now:
            return cached[1]

        response = await self._client.get(f"/products/{product_id}/ticker")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            self._cache[product_id] = (now + self._cache_ttl_s, None)
            return None
        try:
            price = float(payload["price"])
            bid = float(payload["bid"])
            ask = float(payload["ask"])
        except (KeyError, TypeError, ValueError):
            snapshot = None
        else:
            snapshot = SpotTickerSnapshot(
                venue="coinbase",
                symbol=product_id,
                price=price,
                bid=bid,
                ask=ask,
                last_updated_at=_parse_iso_datetime(payload.get("time")),
            )
        self._cache[product_id] = (now + self._cache_ttl_s, snapshot)
        return snapshot


class CoinGeckoSpotClient:
    def __init__(
        self,
        *,
        base_url: str = "https://api.coingecko.com/api/v3",
        timeout_s: float = 10.0,
        cache_ttl_s: float = 20.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self._base, timeout=timeout_s)
        self._cache_ttl_s = cache_ttl_s
        self._cache: dict[str, tuple[float, SpotTickerSnapshot | None]] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> CoinGeckoSpotClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def simple_price(
        self,
        *,
        coin_id: str,
        vs_currency: str = "usd",
    ) -> SpotTickerSnapshot | None:
        cache_key = f"{coin_id}:{vs_currency}".lower()
        now = time.monotonic()
        cached = self._cache.get(cache_key)
        if cached is not None and cached[0] > now:
            return cached[1]

        response = await self._client.get(
            "/simple/price",
            params={
                "ids": coin_id,
                "vs_currencies": vs_currency,
                "include_last_updated_at": "true",
            },
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            self._cache[cache_key] = (now + self._cache_ttl_s, None)
            return None
        row = payload.get(coin_id)
        if not isinstance(row, dict):
            self._cache[cache_key] = (now + self._cache_ttl_s, None)
            return None
        try:
            price = float(row[vs_currency])
        except (KeyError, TypeError, ValueError):
            snapshot = None
        else:
            updated_at = None
            if row.get("last_updated_at") is not None:
                try:
                    updated_at = datetime.fromtimestamp(
                        int(row["last_updated_at"]),
                        tz=UTC,
                    )
                except (TypeError, ValueError, OSError):
                    updated_at = None
            snapshot = SpotTickerSnapshot(
                venue="coingecko",
                symbol=coin_id,
                price=price,
                last_updated_at=updated_at,
            )
        self._cache[cache_key] = (now + self._cache_ttl_s, snapshot)
        return snapshot


def options_implied_probability(
    *,
    strike: float,
    direction: str,
    resolution_date: datetime,
    asked_at: datetime,
    iv: DeribitIV,
) -> float | None:
    sigma = iv.strike_iv if iv.strike_iv > 0 else iv.atm_iv
    if sigma <= 0:
        return None
    return _terminal_probability(
        spot=iv.index_price,
        strike=strike,
        direction=direction,
        resolution_date=resolution_date,
        asked_at=asked_at,
        sigma=sigma,
    )


def perp_basis_implied_probability(
    *,
    strike: float,
    direction: str,
    resolution_date: datetime,
    asked_at: datetime,
    sigma: float,
    snapshot: BinancePerpSnapshot,
) -> float | None:
    if sigma <= 0 or snapshot.index_price <= 0 or snapshot.mark_price <= 0:
        return None
    # Clamp the premium anchor so a stale or spiky perp mark cannot create a
    # nonsensical pseudo-forward far away from spot.
    premium_ratio = max(0.85, min(1.15, snapshot.mark_price / snapshot.index_price))
    adjusted_spot = snapshot.index_price * premium_ratio
    return _terminal_probability(
        spot=adjusted_spot,
        strike=strike,
        direction=direction,
        resolution_date=resolution_date,
        asked_at=asked_at,
        sigma=sigma,
    )


def validate_spot_quotes(
    quotes: list[SpotTickerSnapshot],
    *,
    max_deviation_bps: float,
    min_venues: int = 2,
) -> SpotValidationResult:
    valid_quotes = [quote for quote in quotes if quote.price > 0]
    if not valid_quotes:
        return SpotValidationResult(
            reference_price=None,
            accepted_quotes=[],
            rejected_quotes=[],
            max_divergence_bps=0.0,
            is_valid=False,
        )
    raw_median = float(median(quote.price for quote in valid_quotes))
    accepted: list[SpotTickerSnapshot] = []
    rejected: list[SpotTickerSnapshot] = []
    max_divergence = 0.0
    for quote in valid_quotes:
        divergence_bps = abs(quote.price / raw_median - 1.0) * 10_000.0
        max_divergence = max(max_divergence, divergence_bps)
        if divergence_bps <= max_deviation_bps:
            accepted.append(quote)
        else:
            rejected.append(quote)
    reference_price = (
        float(median(quote.price for quote in accepted))
        if accepted
        else None
    )
    return SpotValidationResult(
        reference_price=reference_price,
        accepted_quotes=accepted,
        rejected_quotes=rejected,
        max_divergence_bps=max_divergence,
        is_valid=len(accepted) >= min_venues and reference_price is not None,
    )


def _terminal_probability(
    *,
    spot: float,
    strike: float,
    direction: str,
    resolution_date: datetime,
    asked_at: datetime,
    sigma: float,
) -> float | None:
    if strike <= 0 or spot <= 0 or sigma <= 0:
        return None
    features = MarketFeatures(
        strike=strike,
        direction=direction,
        resolution_date=resolution_date,
    )
    result = threshold_baseline(
        BaselineInputs(
            features=features,
            asked_at=asked_at.timestamp(),
            spot=spot,
            implied_vol_atm=sigma,
        ),
        mode="terminal",
    )
    return result.probability


def _parse_iso_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
