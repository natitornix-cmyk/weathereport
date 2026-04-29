from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import Station, settings


@dataclass
class Bin:
    bin_lo: float       # °C inclusive
    bin_hi: float       # °C exclusive (or +inf for open-top)
    label: str
    yes_token_id: str
    bid: float          # 0..1
    ask: float          # 0..1
    depth_usd: float    # liquidity proxy
    market_id: str
    condition_id: str


@dataclass
class WeatherEvent:
    event_id: str
    slug: str
    title: str
    station_code: str
    target_date: date
    end_date: datetime
    bins: list[Bin]

    @property
    def url(self) -> str:
        return f"https://polymarket.com/event/{self.slug}"


# Match "32-33°C", "32-33", "32 to 33 ºC", "<33", ">35", "33+", "33 or higher"
_RANGE_RE = re.compile(
    r"(?P<lo>-?\d+(?:\.\d+)?)\s*[-–to]+\s*(?P<hi>-?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_LT_RE = re.compile(r"<\s*(?P<v>-?\d+(?:\.\d+)?)")
_LE_RE = re.compile(r"(?:<=|≤|under|below)\s*(?P<v>-?\d+(?:\.\d+)?)", re.IGNORECASE)
_GT_RE = re.compile(r">\s*(?P<v>-?\d+(?:\.\d+)?)")
_GE_RE = re.compile(r"(?:>=|≥|over|above)\s*(?P<v>-?\d+(?:\.\d+)?)", re.IGNORECASE)
_PLUS_RE = re.compile(r"(?P<v>-?\d+(?:\.\d+)?)\s*\+")
_OR_HIGHER_RE = re.compile(r"(?P<v>-?\d+(?:\.\d+)?)\s*(?:or higher|or more|or above)", re.IGNORECASE)


def parse_bin_label(label: str) -> tuple[float, float] | None:
    if not label:
        return None
    s = label.strip()

    m = _RANGE_RE.search(s)
    if m:
        lo = float(m.group("lo"))
        hi = float(m.group("hi"))
        if hi >= lo:
            # Polymarket convention "32-33°C" usually means [32, 34) inclusive at integer bins.
            # Treat hi as inclusive integer => exclusive boundary at hi+1 if both are ints.
            if lo == int(lo) and hi == int(hi):
                return lo, hi + 1.0
            return lo, hi

    m = _PLUS_RE.search(s)
    if m:
        return float(m.group("v")), float("inf")

    m = _OR_HIGHER_RE.search(s)
    if m:
        return float(m.group("v")), float("inf")

    m = _GE_RE.search(s)
    if m:
        return float(m.group("v")), float("inf")

    m = _GT_RE.search(s)
    if m:
        return float(m.group("v")), float("inf")

    m = _LE_RE.search(s)
    if m:
        return float("-inf"), float(m.group("v")) + 1.0

    m = _LT_RE.search(s)
    if m:
        return float("-inf"), float(m.group("v"))

    return None


def _to_celsius_if_needed(lo: float, hi: float, label: str) -> tuple[float, float]:
    """If the label clearly indicates Fahrenheit, convert to Celsius."""
    s = label.lower()
    if "°f" in s or " f" in s or s.endswith("f"):
        def f2c(x: float) -> float:
            return float("inf") if x == float("inf") else float("-inf") if x == float("-inf") else (x - 32) * 5 / 9
        return f2c(lo), f2c(hi)
    return lo, hi


def _outcome_yes_index(outcomes: list[str]) -> int:
    if not outcomes:
        return 0
    for i, o in enumerate(outcomes):
        if (o or "").strip().lower() == "yes":
            return i
    return 0


def _safe_json_list(s: str | list | None) -> list:
    if s is None:
        return []
    if isinstance(s, list):
        return s
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return []


def _match_station(title: str, stations: list[Station]) -> Station | None:
    t = (title or "").lower()
    for st in stations:
        if st.polymarket_keyword in t:
            return st
    return None


def _parse_target_date(event: dict) -> date | None:
    # Try title first ("on April 29, 2026")
    title = event.get("title") or ""
    m = re.search(
        r"on (?P<m>january|february|march|april|may|june|july|august|september|october|november|december)\s+(?P<d>\d{1,2}),?\s+(?P<y>\d{4})",
        title,
        re.IGNORECASE,
    )
    if m:
        try:
            return datetime.strptime(
                f"{m.group('m')} {m.group('d')} {m.group('y')}",
                "%B %d %Y",
            ).date()
        except ValueError:
            pass
    end = event.get("endDate")
    if end:
        try:
            return datetime.fromisoformat(end.replace("Z", "+00:00")).date()
        except ValueError:
            return None
    return None


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
async def _fetch_events(tag_slug: str = "weather", limit: int = 50) -> list[dict]:
    url = f"{settings.polymarket_gamma_base}/events"
    params = {"active": "true", "closed": "false", "limit": limit, "tag_slug": tag_slug}
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        return r.json()


async def list_active_weather_events() -> list[WeatherEvent]:
    """Return parsed weather events whose station matches our config."""
    raw = await _fetch_events()
    stations = settings.active_stations
    out: list[WeatherEvent] = []
    for ev in raw:
        st = _match_station(ev.get("title", ""), stations)
        if st is None:
            continue
        target = _parse_target_date(ev)
        if target is None:
            continue
        bins: list[Bin] = []
        for mk in ev.get("markets", []):
            if mk.get("closed") or not mk.get("active", True):
                continue
            label = mk.get("groupItemTitle") or mk.get("question") or ""
            parsed = parse_bin_label(label)
            if not parsed:
                continue
            lo, hi = _to_celsius_if_needed(parsed[0], parsed[1], label)

            outcomes = _safe_json_list(mk.get("outcomes"))
            prices = _safe_json_list(mk.get("outcomePrices"))
            tokens = _safe_json_list(mk.get("clobTokenIds"))
            yes_idx = _outcome_yes_index(outcomes) if outcomes else 0
            yes_token = str(tokens[yes_idx]) if yes_idx < len(tokens) else ""
            try:
                yes_price = float(prices[yes_idx]) if yes_idx < len(prices) else 0.5
            except (TypeError, ValueError):
                yes_price = 0.5
            bid = float(mk.get("bestBid") or yes_price)
            ask = float(mk.get("bestAsk") or yes_price)
            depth = float(mk.get("liquidity") or 0.0)
            bins.append(
                Bin(
                    bin_lo=lo,
                    bin_hi=hi,
                    label=label,
                    yes_token_id=yes_token,
                    bid=bid,
                    ask=ask,
                    depth_usd=depth,
                    market_id=str(mk.get("id") or ""),
                    condition_id=str(mk.get("conditionId") or ""),
                )
            )
        if not bins:
            continue
        # Sort by bin_lo for display.
        bins.sort(key=lambda b: (b.bin_lo, b.bin_hi))
        end_dt = ev.get("endDate") or ""
        try:
            end_dt_parsed = datetime.fromisoformat(end_dt.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            end_dt_parsed = datetime.now(timezone.utc)
        out.append(
            WeatherEvent(
                event_id=str(ev.get("id") or ev.get("slug") or ""),
                slug=str(ev.get("slug") or ""),
                title=str(ev.get("title") or ""),
                station_code=st.code,
                target_date=target,
                end_date=end_dt_parsed,
                bins=bins,
            )
        )
    return out
