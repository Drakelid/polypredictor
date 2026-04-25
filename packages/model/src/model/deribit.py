"""Deribit implied-vol client.

PRD §6.3: "Options-implied probability from Deribit ATM + skew (primary
baseline for threshold markets)."

M1 scope: fetch ATM IV and a skew-adjusted strike IV from the public REST
API — no auth required. The endpoint ``/public/get_book_summary_by_currency``
returns per-instrument marks including ``mark_iv``; we pick:

  * ATM: the closest strike to current index price on the nearest expiry on
    or after the resolution horizon.
  * Strike IV: the closest-strike option at or above ``strike`` for calls
    (direction='above') or at or below for puts.

Deribit quotes IV in percentage points; we convert to decimal.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import structlog

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class DeribitIV:
    index_price: float
    expiry_days: float
    realized_vol: float | None   # decimal, latest realized vol from public history
    atm_iv: float              # decimal (0.65 = 65%)
    strike_iv: float           # decimal, interpolated at the requested strike
    atm_instrument: str
    strike_instrument: str


@dataclass(frozen=True)
class DeribitTermStructurePoint:
    currency: str
    expiry_date: datetime
    expiry_days: float
    underlying_price: float
    atm_iv: float
    call_otm_iv: float | None
    put_otm_iv: float | None
    strike_skew: float | None
    atm_instrument: str
    call_otm_instrument: str | None
    put_otm_instrument: str | None


class DeribitClient:
    """Thin async wrapper around Deribit's public REST."""

    def __init__(
        self,
        *,
        base_url: str = "https://www.deribit.com/api/v2",
        timeout_s: float = 10.0,
        cache_ttl_s: float = 30.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self._base, timeout=timeout_s)
        self._cache_ttl_s = cache_ttl_s
        self._book_summary_cache: dict[str, tuple[float, list[dict[str, object]]]] = {}
        self._historical_vol_cache: dict[str, tuple[float, float | None]] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> DeribitClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def fetch_iv(
        self,
        *,
        currency: str,
        strike: float,
        direction: str,          # 'above' → call, 'below' → put
        horizon_days: float,
    ) -> DeribitIV | None:
        """Fetch ATM IV + strike IV for the given currency and horizon.

        Returns ``None`` if Deribit has no usable options for the horizon.
        """
        kind = "call" if direction == "above" else "put"
        rows = await self._book_summary(currency.upper())
        if not isinstance(rows, list) or not rows:
            return None

        index_price = _first_float(rows, "underlying_price")
        if index_price is None or index_price <= 0:
            return None

        # Pick expiry closest to but >= horizon_days.
        target = _pick_nearest_expiry(rows, horizon_days)
        if target is None:
            log.warning("deribit.no_expiry", horizon_days=horizon_days)
            return None
        expiry_ts, expiry_days = target
        same_expiry = [r for r in rows if _parse_instrument(r.get("instrument_name", "")).get("expiry_ts") == expiry_ts]
        if not same_expiry:
            return None

        # ATM: closest strike to index price (any kind — calls/puts are nearly
        # equivalent under put-call parity at the money).
        atm_row = min(
            same_expiry,
            key=lambda r: abs(_parse_instrument(r.get("instrument_name", "")).get("strike", 0) - index_price),
        )
        atm_iv_pct = _mark_iv(atm_row)

        # Strike IV: closest strike on the correct side of moneyness for the given direction.
        kind_matches = [r for r in same_expiry if _parse_instrument(r.get("instrument_name", "")).get("kind") == kind[0].upper()]
        if kind_matches:
            strike_row = min(
                kind_matches,
                key=lambda r: abs(_parse_instrument(r.get("instrument_name", "")).get("strike", 0) - strike),
            )
        else:
            strike_row = atm_row
        strike_iv_pct = _mark_iv(strike_row)

        if atm_iv_pct is None:
            return None
        strike_iv_pct = strike_iv_pct if strike_iv_pct is not None else atm_iv_pct
        realized_vol = await self._historical_volatility(currency.upper())

        return DeribitIV(
            index_price=float(index_price),
            expiry_days=float(expiry_days),
            realized_vol=realized_vol,
            atm_iv=float(atm_iv_pct) / 100.0,
            strike_iv=float(strike_iv_pct) / 100.0,
            atm_instrument=str(atm_row.get("instrument_name", "")),
            strike_instrument=str(strike_row.get("instrument_name", "")),
        )

    async def fetch_term_structure(
        self,
        *,
        currency: str,
    ) -> list[DeribitTermStructurePoint]:
        rows = await self._book_summary(currency.upper())
        if not isinstance(rows, list) or not rows:
            return []
        index_price = _first_float(rows, "underlying_price")
        if index_price is None or index_price <= 0:
            return []

        grouped: dict[int, list[dict[str, object]]] = {}
        for row in rows:
            info = _parse_instrument(str(row.get("instrument_name", "")))
            expiry_ts = info.get("expiry_ts")
            if not isinstance(expiry_ts, int):
                continue
            grouped.setdefault(expiry_ts, []).append(row)

        out: list[DeribitTermStructurePoint] = []
        for expiry_ts, same_expiry in sorted(grouped.items()):
            expiry_dt = _expiry_datetime(expiry_ts)
            if expiry_dt is None:
                continue
            expiry_days = _expiry_days_from_ts(expiry_ts)
            if expiry_days is None:
                continue

            atm_row = min(
                same_expiry,
                key=lambda row: abs(
                    _parse_instrument(row.get("instrument_name", "")).get("strike", 0)
                    - index_price
                ),
            )
            atm_iv_pct = _mark_iv(atm_row)
            if atm_iv_pct is None:
                continue

            call_otm = _nearest_otm_row(
                same_expiry,
                index_price=index_price,
                kind="C",
            )
            put_otm = _nearest_otm_row(
                same_expiry,
                index_price=index_price,
                kind="P",
            )
            call_otm_iv = _mark_iv(call_otm) if call_otm is not None else None
            put_otm_iv = _mark_iv(put_otm) if put_otm is not None else None
            strike_skew = None
            if call_otm_iv is not None and put_otm_iv is not None:
                strike_skew = (call_otm_iv - put_otm_iv) / 100.0

            out.append(
                DeribitTermStructurePoint(
                    currency=currency.upper(),
                    expiry_date=expiry_dt,
                    expiry_days=expiry_days,
                    underlying_price=float(index_price),
                    atm_iv=float(atm_iv_pct) / 100.0,
                    call_otm_iv=(
                        float(call_otm_iv) / 100.0
                        if call_otm_iv is not None
                        else None
                    ),
                    put_otm_iv=(
                        float(put_otm_iv) / 100.0
                        if put_otm_iv is not None
                        else None
                    ),
                    strike_skew=strike_skew,
                    atm_instrument=str(atm_row.get("instrument_name", "")),
                    call_otm_instrument=(
                        str(call_otm.get("instrument_name", ""))
                        if call_otm is not None
                        else None
                    ),
                    put_otm_instrument=(
                        str(put_otm.get("instrument_name", ""))
                        if put_otm is not None
                        else None
                    ),
                )
            )
        return out

    async def _book_summary(self, currency: str) -> list[dict[str, object]]:
        cached = self._book_summary_cache.get(currency)
        now = time.monotonic()
        if cached is not None and cached[0] > now:
            return cached[1]

        r = await self._client.get(
            "/public/get_book_summary_by_currency",
            params={"currency": currency, "kind": "option"},
        )
        r.raise_for_status()
        payload = r.json()
        if not isinstance(payload, dict) or "result" not in payload:
            return []
        rows = payload["result"]
        if not isinstance(rows, list):
            return []
        normalized = [row for row in rows if isinstance(row, dict)]
        self._book_summary_cache[currency] = (now + self._cache_ttl_s, normalized)
        return normalized

    async def _historical_volatility(self, currency: str) -> float | None:
        cached = self._historical_vol_cache.get(currency)
        now = time.monotonic()
        if cached is not None and cached[0] > now:
            return cached[1]

        r = await self._client.get(
            "/public/get_historical_volatility",
            params={"currency": currency},
        )
        r.raise_for_status()
        payload = r.json()
        if not isinstance(payload, dict) or "result" not in payload:
            self._historical_vol_cache[currency] = (now + self._cache_ttl_s, None)
            return None
        latest_pct = _latest_historical_vol_pct(payload["result"])
        realized_vol = (latest_pct / 100.0) if latest_pct is not None else None
        self._historical_vol_cache[currency] = (now + self._cache_ttl_s, realized_vol)
        return realized_vol


# --- Helpers ---------------------------------------------------------------


def _parse_instrument(name: str) -> dict[str, object]:
    """Parse a Deribit instrument name like ``BTC-28JUN26-100000-C`` into
    ``{currency, expiry_ts (unix s), strike, kind}``.

    We only need strike + kind + a coarse expiry comparator, so we parse
    the date as calendar-months-and-day. Returns empty dict on malformed.
    """
    parts = name.split("-")
    if len(parts) != 4:
        return {}
    _, date_str, strike_str, kind_str = parts
    try:
        strike = float(strike_str)
    except ValueError:
        return {}
    # Convert e.g. "28JUN26" to a comparable integer (not an exact timestamp,
    # but monotonic in expiry — good enough to pick "nearest >= horizon").
    try:
        day = int(date_str[:2])
        month = _MONTHS.get(date_str[2:5].upper())
        year = 2000 + int(date_str[5:])
        if month is None:
            return {}
        expiry_ts = year * 10_000 + month * 100 + day
    except (ValueError, IndexError):
        return {}
    return {"strike": strike, "kind": kind_str[:1], "expiry_ts": expiry_ts}


_MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}


def _mark_iv(row: dict[str, object]) -> float | None:
    v = row.get("mark_iv")
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _first_float(rows: list[dict[str, object]], key: str) -> float | None:
    for r in rows:
        v = r.get(key)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def _pick_nearest_expiry(
    rows: list[dict[str, object]], horizon_days: float
) -> tuple[int, float] | None:
    """Return (expiry_ts_sentinel, expiry_days_from_now) for the expiry that
    is closest to but on-or-after ``horizon_days``. Falls back to the longest
    expiry available if none covers the horizon.

    Because we derive ``expiry_ts`` as a YYYYMMDD integer rather than a true
    epoch, we convert back to "days from now" by a best-effort heuristic —
    the exact number matters less than picking the right bucket.
    """
    seen: dict[int, int] = {}
    for r in rows:
        info = _parse_instrument(str(r.get("instrument_name", "")))
        ts = info.get("expiry_ts")
        if isinstance(ts, int):
            seen[ts] = seen.get(ts, 0) + 1
    if not seen:
        return None
    # Crude days-from-now: the YYYYMMDD difference is NOT a day count — a 31-day
    # month would bake in ~70 because of the tens-digit. Convert via datetime.
    ranked = []
    for ts in seen:
        days = _expiry_days_from_ts(ts)
        if days is None:
            continue
        ranked.append((ts, days))
    if not ranked:
        return None
    on_or_after = [(ts, d) for ts, d in ranked if d >= horizon_days]
    if on_or_after:
        return min(on_or_after, key=lambda x: x[1])
    return max(ranked, key=lambda x: x[1])


def _nearest_otm_row(
    rows: list[dict[str, object]],
    *,
    index_price: float,
    kind: str,
) -> dict[str, object] | None:
    candidates: list[tuple[float, dict[str, object]]] = []
    for row in rows:
        info = _parse_instrument(str(row.get("instrument_name", "")))
        strike = info.get("strike")
        row_kind = info.get("kind")
        if not isinstance(strike, float) or row_kind != kind:
            continue
        if kind == "C" and strike <= index_price:
            continue
        if kind == "P" and strike >= index_price:
            continue
        candidates.append((abs(strike - index_price), row))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def _expiry_datetime(expiry_ts: int) -> datetime | None:
    year, rest = divmod(expiry_ts, 10_000)
    month, day = divmod(rest, 100)
    try:
        return datetime(year, month, day, tzinfo=UTC)
    except ValueError:
        return None


def _expiry_days_from_ts(expiry_ts: int) -> float | None:
    expiry_dt = _expiry_datetime(expiry_ts)
    if expiry_dt is None:
        return None
    today = datetime.now(tz=UTC)
    return (expiry_dt - today).total_seconds() / 86_400


def _latest_historical_vol_pct(rows: object) -> float | None:
    if not isinstance(rows, list):
        return None
    latest_ts = -1
    latest_val: float | None = None
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        try:
            ts = int(row[0])
            val = float(row[1])
        except (TypeError, ValueError):
            continue
        if ts >= latest_ts:
            latest_ts = ts
            latest_val = val
    return latest_val
