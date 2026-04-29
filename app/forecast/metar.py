from __future__ import annotations

import re
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

# aviationweather.gov returns CSV/JSON for METAR. We use JSON.
AWC_URL = "https://aviationweather.gov/api/data/metar"


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
async def fetch_recent_metars(icao: str, hours: int = 36) -> list[dict]:
    """Recent METARs as list of dicts with keys: time, temp_c."""
    params = {"ids": icao, "format": "json", "hours": hours}
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "weathereport/0.1"}) as client:
        r = await client.get(AWC_URL, params=params)
        r.raise_for_status()
        rows = r.json()

    out = []
    for row in rows:
        t = row.get("reportTime") or row.get("obsTime")
        temp = row.get("temp")
        if t is None or temp is None:
            # Fall back to parsing the raw METAR.
            raw = row.get("rawOb") or ""
            t_parsed, temp_parsed = _parse_raw_metar(raw)
            t = t or t_parsed
            temp = temp if temp is not None else temp_parsed
        if t is None or temp is None:
            continue
        if isinstance(t, str):
            try:
                t_dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
            except ValueError:
                continue
        else:
            t_dt = datetime.fromtimestamp(int(t), tz=timezone.utc)
        out.append({"time": t_dt, "temp_c": float(temp)})
    return out


def running_max_for_local_day(metars: list[dict], target_date: date, tz: str) -> tuple[float | None, datetime | None]:
    z = ZoneInfo(tz)
    best = None
    best_t = None
    for m in metars:
        local = m["time"].astimezone(z)
        if local.date() != target_date:
            continue
        if best is None or m["temp_c"] > best:
            best = m["temp_c"]
            best_t = m["time"]
    return best, best_t


_TEMP_RE = re.compile(r"\b(M?\d{2})/(M?\d{2})\b")
_TIME_RE = re.compile(r"\b(\d{2})(\d{2})(\d{2})Z\b")


def _parse_raw_metar(raw: str) -> tuple[datetime | None, float | None]:
    if not raw:
        return None, None
    t = None
    tm = _TIME_RE.search(raw)
    if tm:
        day, hh, mm = int(tm.group(1)), int(tm.group(2)), int(tm.group(3))
        now = datetime.now(timezone.utc)
        try:
            t = now.replace(day=day, hour=hh, minute=mm, second=0, microsecond=0)
            if t > now:
                # rolled past month boundary
                t = t.replace(month=t.month - 1 if t.month > 1 else 12)
        except ValueError:
            t = None
    temp = None
    mm = _TEMP_RE.search(raw)
    if mm:
        s = mm.group(1)
        v = int(s.replace("M", "-"))
        temp = float(v)
    return t, temp
