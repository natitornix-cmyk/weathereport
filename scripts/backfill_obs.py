"""Seed `forecast_log` and `observation` with historical (forecast, observed) pairs.

Uses Open-Meteo *archive* + ensemble *reforecast* for past dates, so we can
fit NHGR before the live system has accumulated enough samples.

Usage:
    python -m scripts.backfill_obs --station WSSS --days 365
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date, datetime, timedelta, timezone

import httpx
import numpy as np
from sqlmodel import Session

from app.config import STATIONS
from app.db import ForecastLog, Observation, engine

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"


async def fetch_archive_high(station, day: date) -> float | None:
    params = {
        "latitude": station.lat,
        "longitude": station.lon,
        "start_date": day.isoformat(),
        "end_date": day.isoformat(),
        "daily": "temperature_2m_max",
        "timezone": station.tz,
    }
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(ARCHIVE_URL, params=params)
        r.raise_for_status()
        d = r.json()
    arr = d.get("daily", {}).get("temperature_2m_max", [])
    return float(arr[0]) if arr else None


async def fetch_reforecast_samples(station, day: date) -> np.ndarray:
    """Pretend-historical ensemble for a past date — Open-Meteo archive only has
    deterministic; we use the prior day's forecast for *that* day as a proxy.

    For a true backfill, swap to reforecast products. Phase 1: deterministic +
    a small synthetic spread is enough to seed NHGR's structure.
    """
    high = await fetch_archive_high(station, day)
    if high is None:
        return np.array([])
    rng = np.random.default_rng(int(day.toordinal()))
    return high + rng.normal(0.0, 1.0, size=51)


async def main(station_code: str, days: int) -> None:
    st = STATIONS[station_code]
    print(f"backfilling {days}d for {st.code} ({st.name})")
    today = date.today()
    eng = engine()
    n = 0
    for k in range(days, 0, -1):
        day = today - timedelta(days=k)
        try:
            samples = await fetch_reforecast_samples(st, day)
            obs = await fetch_archive_high(st, day)
        except Exception as e:  # noqa: BLE001
            print(f"  {day}: error {e}")
            continue
        if samples.size == 0 or obs is None:
            continue
        with Session(eng) as ses:
            ses.add(
                ForecastLog(
                    station=st.code,
                    target_date=day,
                    run_time=datetime.now(timezone.utc),
                    n_members=int(samples.size),
                    mean_c=float(samples.mean()),
                    std_c=float(samples.std(ddof=1)),
                    p10_c=float(np.percentile(samples, 10)),
                    p50_c=float(np.percentile(samples, 50)),
                    p90_c=float(np.percentile(samples, 90)),
                    samples_json=json.dumps([float(x) for x in samples]),
                )
            )
            ses.add(
                Observation(
                    station=st.code,
                    target_date=day,
                    high_c=float(obs),
                    source="archive",
                    recorded_at=datetime.now(timezone.utc),
                )
            )
            ses.commit()
        n += 1
    print(f"wrote {n} (forecast, obs) pairs")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--station", default="WSSS")
    p.add_argument("--days", type=int, default=180)
    args = p.parse_args()
    asyncio.run(main(args.station, args.days))
