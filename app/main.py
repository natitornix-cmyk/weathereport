from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, desc, select

from .config import settings
from .db import Recommendation, engine
from .scheduler import build_scheduler, run_pipeline_once
from .telegram_bot import start_bot, stop_bot

log = logging.getLogger("weathereport")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure DB exists.
    engine()
    bot_task = asyncio.create_task(start_bot())
    sched = build_scheduler()
    sched.start()
    log.info("scheduler started; pipeline will run on cron")
    # Kick off an initial run so the dashboard isn't empty.
    asyncio.create_task(_safe_initial_run())
    try:
        yield
    finally:
        sched.shutdown(wait=False)
        await stop_bot()
        bot_task.cancel()


async def _safe_initial_run():
    try:
        await run_pipeline_once()
    except Exception as e:  # noqa: BLE001
        log.exception("initial pipeline run failed: %s", e)


app = FastAPI(title="weathereport", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


def _latest_recommendations(limit: int = 50) -> list[Recommendation]:
    with Session(engine()) as ses:
        rows = ses.exec(
            select(Recommendation).order_by(desc(Recommendation.created_at)).limit(limit)
        ).all()
    # Group by (event_id, target_date), keep only most recent rec per bin.
    seen: dict[tuple[str, str], Recommendation] = {}
    for r in rows:
        key = (r.event_id, r.bin_label)
        if key not in seen:
            seen[key] = r
    out = list(seen.values())
    out.sort(key=lambda r: (r.target_date, -r.edge))
    return out


@app.get("/healthz")
def healthz():
    return {"ok": True, "ts": datetime.now(timezone.utc).isoformat()}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    recs = _latest_recommendations()
    # Group by event.
    grouped: dict[str, dict] = {}
    for r in recs:
        g = grouped.setdefault(
            r.event_id,
            {
                "title": r.event_title,
                "station": r.station,
                "target_date": r.target_date,
                "url": r.polymarket_url,
                "bins": [],
            },
        )
        g["bins"].append(r)
    events = sorted(
        grouped.values(),
        key=lambda e: (e["target_date"], -max((b.edge for b in e["bins"]), default=0)),
    )
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "events": events,
            "bankroll": settings.bankroll_usd,
            "kelly_frac": settings.kelly_fraction,
            "edge_threshold": settings.edge_threshold,
            "now": datetime.now(timezone.utc),
        },
    )


@app.get("/api/markets")
def api_markets():
    recs = _latest_recommendations()
    return JSONResponse(
        [
            {
                "event_id": r.event_id,
                "event_title": r.event_title,
                "station": r.station,
                "target_date": r.target_date.isoformat(),
                "bin": r.bin_label,
                "our_prob": round(r.our_prob, 4),
                "ask": round(r.market_ask, 4),
                "edge": round(r.edge, 4),
                "stake_usd": round(r.stake_usd, 2),
                "url": r.polymarket_url,
            }
            for r in recs
        ]
    )


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(str(BASE_DIR / "static" / "manifest.webmanifest"), media_type="application/manifest+json")


@app.get("/sw.js")
def sw():
    return FileResponse(str(BASE_DIR / "static" / "sw.js"), media_type="application/javascript")


@app.post("/api/run-now")
async def run_now():
    """Trigger an out-of-band pipeline run (handy for debugging)."""
    asyncio.create_task(_safe_initial_run())
    return {"ok": True}
