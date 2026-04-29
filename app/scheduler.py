from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Iterable

import numpy as np
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlmodel import Session, select

from .calibration import NHGRParams, fit_nhgr, load_history
from .config import Station, settings
from .db import (
    AlertSent,
    ForecastLog,
    MarketSnapshot,
    Recommendation as RecRow,
    engine,
)
from .forecast.cdf import CDF, samples_to_cdf
from .forecast.metar import fetch_recent_metars, running_max_for_local_day
from .forecast.openmeteo import fetch_ensemble
from .polymarket import WeatherEvent, list_active_weather_events
from .pricing import Recommendation, compute_recommendations
from .telegram_bot import push_alert

log = logging.getLogger("weathereport.scheduler")


def build_scheduler() -> AsyncIOScheduler:
    sched = AsyncIOScheduler(timezone="UTC")
    sched.add_job(run_pipeline_once, "interval", minutes=15, id="pipeline", coalesce=True, max_instances=1)
    sched.add_job(intraday_metar_update, "interval", minutes=30, id="metar", coalesce=True, max_instances=1)
    return sched


async def run_pipeline_once() -> int:
    """Full pass: fetch events, fetch ensemble per station/date, compute recs, persist, alert."""
    events = await list_active_weather_events()
    if not events:
        log.info("no active weather events matched configured stations")
        return 0

    recs_total = 0
    by_station = {st.code: st for st in settings.active_stations}
    cache: dict[tuple[str, str], CDF] = {}

    for ev in events:
        st = by_station.get(ev.station_code)
        if not st:
            continue
        cdf = await _get_or_build_cdf(st, ev.target_date.isoformat(), cache)
        if cdf is None:
            continue
        recs = compute_recommendations(cdf, ev)
        _persist_market_snapshots(ev)
        if recs:
            _persist_recommendations(recs)
            await _maybe_alert(recs)
            recs_total += len(recs)

    log.info("pipeline pass: %d events, %d recommendations", len(events), recs_total)
    return recs_total


async def _get_or_build_cdf(st: Station, target_iso: str, cache: dict) -> CDF | None:
    key = (st.code, target_iso)
    if key in cache:
        return cache[key]
    try:
        from datetime import date as _date
        target = _date.fromisoformat(target_iso)
        samples = await fetch_ensemble(st, target)
    except Exception as e:  # noqa: BLE001
        log.warning("ensemble fetch failed for %s %s: %s", st.code, target_iso, e)
        return None
    if samples.size == 0:
        return None
    # Apply NHGR calibration if we have history.
    history = load_history(st.code)
    params = fit_nhgr(history) if len(history) >= 60 else NHGRParams.identity()
    calibrated = params.apply_to_samples(samples)
    cdf = samples_to_cdf(calibrated, sigma_inflate_c=settings.sigma_inflate_c)
    _persist_forecast_log(st, target_iso, calibrated)
    cache[key] = cdf
    return cdf


def _persist_forecast_log(st: Station, target_iso: str, samples: np.ndarray) -> None:
    from datetime import date as _date
    with Session(engine()) as ses:
        ses.add(
            ForecastLog(
                station=st.code,
                target_date=_date.fromisoformat(target_iso),
                run_time=datetime.now(timezone.utc),
                n_members=int(samples.size),
                mean_c=float(samples.mean()),
                std_c=float(samples.std(ddof=1)) if samples.size > 1 else 0.0,
                p10_c=float(np.percentile(samples, 10)),
                p50_c=float(np.percentile(samples, 50)),
                p90_c=float(np.percentile(samples, 90)),
                samples_json=json.dumps([float(x) for x in samples]),
            )
        )
        ses.commit()


def _persist_market_snapshots(ev: WeatherEvent) -> None:
    now = datetime.now(timezone.utc)
    with Session(engine()) as ses:
        for b in ev.bins:
            ses.add(
                MarketSnapshot(
                    event_id=ev.event_id,
                    event_slug=ev.slug,
                    event_title=ev.title,
                    station=ev.station_code,
                    target_date=ev.target_date,
                    bin_lo=b.bin_lo if b.bin_lo != float("-inf") else -999.0,
                    bin_hi=b.bin_hi if b.bin_hi != float("inf") else 999.0,
                    bin_label=b.label,
                    yes_token_id=b.yes_token_id,
                    bid=b.bid,
                    ask=b.ask,
                    depth_usd=b.depth_usd,
                    snapshot_time=now,
                )
            )
        ses.commit()


def _persist_recommendations(recs: Iterable[Recommendation]) -> None:
    now = datetime.now(timezone.utc)
    with Session(engine()) as ses:
        for r in recs:
            ses.add(
                RecRow(
                    event_id=r.event.event_id,
                    event_slug=r.event.slug,
                    event_title=r.event.title,
                    station=r.event.station_code,
                    target_date=r.event.target_date,
                    bin_lo=r.bin.bin_lo if r.bin.bin_lo != float("-inf") else -999.0,
                    bin_hi=r.bin.bin_hi if r.bin.bin_hi != float("inf") else 999.0,
                    bin_label=r.bin.label,
                    our_prob=r.our_prob,
                    market_ask=r.market_ask,
                    edge=r.edge,
                    stake_usd=r.stake_usd,
                    yes_token_id=r.bin.yes_token_id,
                    polymarket_url=r.polymarket_url,
                    created_at=now,
                )
            )
        ses.commit()


async def _maybe_alert(recs: list[Recommendation]) -> None:
    now = datetime.now(timezone.utc)
    bucket = now.strftime("%Y%m%dT%H")
    with Session(engine()) as ses:
        for r in recs:
            key = f"{r.event.event_id}|{r.bin.label}|{bucket}"
            existing = ses.exec(
                select(AlertSent).where(AlertSent.dedup_key == key)
            ).first()
            if existing is not None:
                continue
            await push_alert(r)
            ses.add(AlertSent(dedup_key=key, sent_at=now))
        ses.commit()


async def intraday_metar_update() -> None:
    """For each active station, pull METAR and refine the in-memory CDF cache."""
    # In Phase 1 we just log running max into Observation table when day rolls over;
    # the next pipeline pass will incorporate it via _get_or_build_cdf when extended.
    from datetime import date as _date
    today_utc = _date.today()
    for st in settings.active_stations:
        try:
            metars = await fetch_recent_metars(st.code, hours=36)
        except Exception as e:  # noqa: BLE001
            log.warning("metar fetch failed for %s: %s", st.code, e)
            continue
        running, t = running_max_for_local_day(metars, today_utc, st.tz)
        if running is None:
            continue
        log.info("%s running daily max so far: %.1f°C at %s", st.code, running, t)
