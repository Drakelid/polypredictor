"""CME FedWatch end-of-day client.

Uses CME's official OAuth-protected REST API for FOMC meeting dates and
forecasted target-rate ranges. The API is optional at runtime; when creds are
absent or the question cannot be mapped to a concrete FedWatch contract, the
caller should fall back to file-backed/manual discrete inputs.
"""

from __future__ import annotations

import base64
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx

from .discrete_inputs import DiscreteBaselineRecord


@dataclass(frozen=True)
class FedWatchMeeting:
    meeting_dt: str
    offset_day_count: int | None = None
    lower_rt: int | None = None
    upper_rt: int | None = None


@dataclass(frozen=True)
class FedWatchRateRange:
    lower_rt: int
    upper_rt: int
    probability: float


@dataclass(frozen=True)
class FedWatchForecast:
    meeting_dt: str
    reporting_dt: str | None
    rate_ranges: list[FedWatchRateRange]


class CMEFedWatchClient:
    def __init__(
        self,
        *,
        base_url: str = "https://markets.api.cmegroup.com/fedwatch/v1",
        token_url: str = "https://auth.cmegroup.com/as/token.oauth2",
        api_id: str | None = None,
        api_secret: str | None = None,
        application_name: str = "PolyPredictor",
        application_vendor: str = "PolyPredictor",
        application_version: str = "0.0.1",
        user_agent: str = "PolyPredictor/0.0.1",
        timeout_s: float = 10.0,
        cache_ttl_s: float = 300.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token_url = token_url
        self._api_id = api_id
        self._api_secret = api_secret
        self._application_name = application_name
        self._application_vendor = application_vendor
        self._application_version = application_version
        self._user_agent = user_agent
        self._cache_ttl_s = cache_ttl_s
        self._client = httpx.AsyncClient(timeout=timeout_s)
        self._access_token: str | None = None
        self._token_deadline = 0.0
        self._cache: dict[tuple[str, tuple[tuple[str, str], ...]], tuple[float, Any]] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> CMEFedWatchClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    @property
    def enabled(self) -> bool:
        return bool(self._api_id and self._api_secret)

    async def implied_record_for_market(
        self,
        *,
        question: str,
        slug: str | None = None,
        now: datetime | None = None,
    ) -> DiscreteBaselineRecord | None:
        if not self.enabled:
            return None

        text = " ".join(part for part in [question, slug or ""] if part).strip()
        action = _parse_fomc_action(text)
        if action is None:
            return None

        current_range = await self._current_target_range()
        if current_range is None:
            return None

        meeting = await self._resolve_meeting(text, now=now)
        if meeting is None:
            return None

        forecast = await self._forecast_for_meeting(meeting.meeting_dt)
        if forecast is None or not forecast.rate_ranges:
            return None

        probability = _probability_for_action(
            forecast.rate_ranges,
            current_lower=current_range.lower_rt,
            current_upper=current_range.upper_rt,
            action=action,
        )
        if probability is None:
            return None

        return DiscreteBaselineRecord(
            fedwatch_prob=probability,
            source=f"cme_fedwatch:{action.label}",
            as_of=forecast.reporting_dt,
        )

    async def _resolve_meeting(
        self,
        text: str,
        *,
        now: datetime | None,
    ) -> FedWatchMeeting | None:
        meetings = await self._future_meetings(limit=12)
        if not meetings:
            return None

        target = _parse_meeting_hint(text, now=now)
        if target is None:
            return meetings[0]

        year, month = target
        for meeting in meetings:
            dt = _parse_iso_date(meeting.meeting_dt)
            if dt is not None and dt.year == year and dt.month == month:
                return meeting
        return None

    async def _future_meetings(self, *, limit: int = 12) -> list[FedWatchMeeting]:
        payload = await self._get_json("/meetings/future", params={"limit": str(limit)})
        meetings: list[FedWatchMeeting] = []
        for row in _payload_rows(payload):
            meeting_dt = _opt_str(row.get("meetingDt"))
            if meeting_dt is None:
                continue
            meetings.append(
                FedWatchMeeting(
                    meeting_dt=meeting_dt,
                    offset_day_count=_opt_int(row.get("offsetDayCount")),
                )
            )
        meetings.sort(key=lambda meeting: meeting.meeting_dt)
        return meetings

    async def _current_target_range(self) -> FedWatchMeeting | None:
        payload = await self._get_json("/meetings/history", params={"limit": "1"})
        rows = [
            FedWatchMeeting(
                meeting_dt=meeting_dt,
                offset_day_count=_opt_int(row.get("offsetDayCount")),
                lower_rt=_opt_int(row.get("lowerRt")),
                upper_rt=_opt_int(row.get("upperRt")),
            )
            for row in _payload_rows(payload)
            if (meeting_dt := _opt_str(row.get("meetingDt"))) is not None
        ]
        if not rows:
            return None
        rows.sort(key=lambda meeting: meeting.meeting_dt, reverse=True)
        latest = rows[0]
        if latest.lower_rt is None or latest.upper_rt is None:
            return None
        return latest

    async def _forecast_for_meeting(self, meeting_dt: str) -> FedWatchForecast | None:
        payload = await self._get_json("/forecasts", params={"meetingDt": meeting_dt})
        forecasts: list[FedWatchForecast] = []
        for row in _payload_rows(payload):
            row_meeting_dt = _opt_str(row.get("meetingDt"))
            if row_meeting_dt is None:
                continue
            ranges: list[FedWatchRateRange] = []
            raw_ranges = row.get("rateRange")
            if isinstance(raw_ranges, list):
                for raw in raw_ranges:
                    if not isinstance(raw, dict):
                        continue
                    lower_rt = _opt_int(raw.get("lowerRt"))
                    upper_rt = _opt_int(raw.get("upperRt"))
                    probability = _opt_float(raw.get("probability"))
                    if lower_rt is None or upper_rt is None or probability is None:
                        continue
                    ranges.append(
                        FedWatchRateRange(
                            lower_rt=lower_rt,
                            upper_rt=upper_rt,
                            probability=probability,
                        )
                    )
            forecasts.append(
                FedWatchForecast(
                    meeting_dt=row_meeting_dt,
                    reporting_dt=_opt_str(row.get("reportingDt")),
                    rate_ranges=ranges,
                )
            )
        if not forecasts:
            return None
        forecasts.sort(key=lambda forecast: (forecast.meeting_dt, forecast.reporting_dt or ""))
        return forecasts[-1]

    async def _get_json(self, path: str, *, params: dict[str, str]) -> object:
        key = (path, tuple(sorted(params.items())))
        cached = self._cache.get(key)
        now = time.monotonic()
        if cached is not None and cached[0] > now:
            return cached[1]

        token = await self._token()
        headers = {
            "Authorization": f"Bearer {token}",
            "CME-Application-Name": self._application_name,
            "CME-Application-Vendor": self._application_vendor,
            "CME-Application-Version": self._application_version,
            "CME-Request-ID": str(uuid4()),
            "CME-Transact-Time": datetime.now(tz=UTC).isoformat(),
            "User-Agent": self._user_agent,
        }
        response = await self._client.get(f"{self._base_url}{path}", params=params, headers=headers)
        response.raise_for_status()
        payload = response.json()
        self._cache[key] = (now + self._cache_ttl_s, payload)
        return payload

    async def _token(self) -> str:
        now = time.monotonic()
        if self._access_token is not None and now < self._token_deadline:
            return self._access_token
        if not self.enabled:
            raise RuntimeError("CME FedWatch client is not configured")

        basic = base64.b64encode(f"{self._api_id}:{self._api_secret}".encode()).decode("ascii")
        response = await self._client.post(
            self._token_url,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": f"Basic {basic}",
            },
            content="grant_type=client_credentials",
        )
        response.raise_for_status()
        payload = response.json()
        token = _opt_str(payload.get("access_token")) if isinstance(payload, dict) else None
        expires_in = _opt_int(payload.get("expires_in")) if isinstance(payload, dict) else None
        if token is None or expires_in is None:
            raise RuntimeError("CME OAuth response missing access token")
        self._access_token = token
        self._token_deadline = now + max(60, expires_in - 60)
        return token


@dataclass(frozen=True)
class _FomcAction:
    label: str
    bps: int | None = None


_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def _parse_fomc_action(text: str) -> _FomcAction | None:
    lowered = text.lower()
    bps = _parse_bps(lowered)
    if any(token in lowered for token in ("no change", "unchanged", "hold", "holds", "pause", "stay at", "remain at")):
        return _FomcAction("hold")
    if any(token in lowered for token in ("cut", "cuts", "lower", "lowers")):
        return _FomcAction("cut", bps=bps)
    if any(token in lowered for token in ("hike", "hikes", "raise", "raises", "increase", "increases")):
        return _FomcAction("hike", bps=bps)
    return None


def _parse_bps(text: str) -> int | None:
    match = re.search(r"(?P<bps>\d{1,3})\s*(?:bp|bps|basis points?)\b", text)
    if match is None:
        return None
    return int(match.group("bps"))


def _parse_meeting_hint(
    text: str,
    *,
    now: datetime | None,
) -> tuple[int, int] | None:
    lowered = text.lower().replace("-", " ")
    explicit_year_match = re.search(r"\b(20\d{2})\b", lowered)
    explicit_year = int(explicit_year_match.group(1)) if explicit_year_match else None
    for token, month in _MONTHS.items():
        if re.search(rf"\b{re.escape(token)}\b", lowered):
            year = explicit_year
            if year is None:
                base = now or datetime.now(tz=UTC)
                year = base.year
                if month < base.month - 1:
                    year += 1
            return (year, month)
    return None


def _probability_for_action(
    rate_ranges: list[FedWatchRateRange],
    *,
    current_lower: int,
    current_upper: int,
    action: _FomcAction,
) -> float | None:
    total = 0.0
    matched = False
    for rate_range in rate_ranges:
        if _rate_range_matches_action(
            rate_range,
            current_lower=current_lower,
            current_upper=current_upper,
            action=action,
        ):
            total += rate_range.probability
            matched = True
    if not matched:
        return None
    return max(0.0, min(1.0, total))


def _rate_range_matches_action(
    rate_range: FedWatchRateRange,
    *,
    current_lower: int,
    current_upper: int,
    action: _FomcAction,
) -> bool:
    delta_lower = rate_range.lower_rt - current_lower
    delta_upper = rate_range.upper_rt - current_upper
    if action.label == "hold":
        return delta_lower == 0 and delta_upper == 0
    if action.label == "cut":
        if action.bps is not None:
            return delta_lower == -action.bps and delta_upper == -action.bps
        return delta_lower < 0 and delta_upper < 0
    if action.label == "hike":
        if action.bps is not None:
            return delta_lower == action.bps and delta_upper == action.bps
        return delta_lower > 0 and delta_upper > 0
    return False


def _payload_rows(payload: object) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []

    for key in ("content", "elements", "items", "data", "result"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]

    if "page" in payload and isinstance(payload["page"], dict):
        page = payload["page"]
        for key in ("content", "elements", "items"):
            value = page.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]

    if "meetingDt" in payload:
        return [payload] if isinstance(payload, dict) else []
    return []


def _parse_iso_date(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value).replace(tzinfo=UTC)
    except ValueError:
        return None


def _opt_str(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _opt_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _opt_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
