"""One-shot pipeline run for debugging.

Usage:
    python -m scripts.one_shot --station WSSS
    python -m scripts.one_shot --station WSSS --force-alert
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import date

import numpy as np

from app.config import STATIONS, settings
from app.forecast.cdf import samples_to_cdf
from app.forecast.openmeteo import fetch_ensemble
from app.polymarket import list_active_weather_events
from app.pricing import compute_recommendations
from app.telegram_bot import push_alert, start_bot, stop_bot


async def main(station_code: str, target: date, force_alert: bool) -> None:
    st = STATIONS[station_code]
    print(f"== station {st.code} ({st.name}) target {target}")
    samples = await fetch_ensemble(st, target)
    print(f"ensemble: n={samples.size}  mean={samples.mean():.2f}°C  "
          f"std={samples.std(ddof=1):.2f}°C  "
          f"p10={np.percentile(samples,10):.2f}  p50={np.percentile(samples,50):.2f}  "
          f"p90={np.percentile(samples,90):.2f}")
    cdf = samples_to_cdf(samples, sigma_inflate_c=settings.sigma_inflate_c)

    events = await list_active_weather_events()
    matched = [e for e in events if e.station_code == st.code and e.target_date == target]
    if not matched:
        print(f"no Polymarket event for {st.code} on {target}")
        return
    for ev in matched:
        print(f"\n-- {ev.title} -> {ev.url}")
        recs = compute_recommendations(cdf, ev)
        print(f"{'bin':>14}  {'p':>5}  {'ask':>5}  {'edge':>6}  {'stake':>7}")
        for b in ev.bins:
            p = cdf.prob_between(b.bin_lo, b.bin_hi)
            print(f"{b.label:>14}  {p:5.2f}  {b.ask:5.2f}  {p - b.ask - settings.polymarket_taker_fee*b.ask:+6.2f}  ", end="")
            r = next((rr for rr in recs if rr.bin.label == b.label), None)
            print(f"${r.stake_usd:6.0f}" if r else "      -")
        if force_alert and recs:
            await start_bot()
            await push_alert(recs[0])
            await stop_bot()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--station", default="WSSS")
    p.add_argument("--date", default=None, help="ISO date, default today in station tz")
    p.add_argument("--force-alert", action="store_true")
    args = p.parse_args()
    if args.date:
        target = date.fromisoformat(args.date)
    else:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        target = datetime.now(ZoneInfo(STATIONS[args.station].tz)).date()
    asyncio.run(main(args.station, target, args.force_alert))
