from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import httpx
import numpy as np
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import Station

ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"

# Open-Meteo ensemble models. ECMWF IFS 0.25° (50 members) + GFS ensemble (31 members).
# We pick the seamless flavors that work globally and don't require US grids.
_MODELS = "ecmwf_ifs025,gfs_seamless"


def _local_day_window_utc(target_date: date, tz: str) -> tuple[datetime, datetime]:
    z = ZoneInfo(tz)
    start_local = datetime.combine(target_date, time(0, 0), tzinfo=z)
    end_local = datetime.combine(target_date, time(23, 0), tzinfo=z)
    return start_local.astimezone(ZoneInfo("UTC")), end_local.astimezone(ZoneInfo("UTC"))


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
async def fetch_ensemble(station: Station, target_date: date) -> np.ndarray:
    """Return per-member daily-max temperature in °C for `target_date` at `station`.

    Pulls 24h of hourly 2m-temperature for every ensemble member, then per-member max.
    """
    # Pad request a couple days so members converge to the local-day window.
    params = {
        "latitude": station.lat,
        "longitude": station.lon,
        "hourly": "temperature_2m",
        "models": _MODELS,
        "start_date": target_date.isoformat(),
        "end_date": target_date.isoformat(),
        "timezone": station.tz,
        "temperature_unit": "celsius",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(ENSEMBLE_URL, params=params)
        r.raise_for_status()
        data = r.json()

    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    if not times:
        raise RuntimeError("Open-Meteo returned no hourly data")

    member_series: list[list[float]] = []
    # Open-Meteo returns the deterministic series under "temperature_2m" plus
    # perturbed members under "temperature_2m_member01", "..._member02", etc.
    for k, v in hourly.items():
        if k == "time":
            continue
        if not k.startswith("temperature_2m"):
            continue
        if v is None:
            continue
        arr = [x for x in v if x is not None]
        if arr:
            member_series.append([float(x) for x in arr])

    if not member_series:
        raise RuntimeError("Open-Meteo returned no temperature members")

    max_per_member = np.array([max(s) for s in member_series], dtype=float)
    return max_per_member
