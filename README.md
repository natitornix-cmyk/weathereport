# weathereport

Polymarket weather-market edge engine — recommend-only PWA + Telegram alerts, designed to be used from an iPhone.

It pulls Open-Meteo ensemble forecasts (ECMWF IFS + GFS), turns them into a calibrated CDF over the daily-high temperature, compares the implied bin probabilities to Polymarket bin asks, applies a 1.25% taker-fee adjustment, and surfaces fractional-Kelly stakes for any bin where the post-fee edge is ≥ 3¢.

## What it does

- Targets configurable stations (default: Singapore Changi `WSSS`, NYC LaGuardia `KLGA`).
- Fetches active Polymarket weather events via the public Gamma API and parses bin labels (`32-33°C`, `<28`, `35+`, …).
- Builds a Gaussian-KDE CDF per ensemble pass, with a small dispersion bump and optional NHGR calibration once you have history.
- Computes per-bin edge and quarter-Kelly stake, capped at 2% bankroll/bin and 5% bankroll/event.
- Logs every forecast, market snapshot and recommendation to SQLite for back-testing.
- Pushes Telegram alerts when an unseen bin crosses the edge threshold.
- Serves a mobile-first dashboard you add to your iPhone home screen as a PWA.

## Quick start (local)

```bash
python3 -m pip install -e .
cp .env.example .env
# fill in TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID later if you want alerts
python3 -m uvicorn app.main:app --reload --port 8080
```

Open `http://localhost:8080` — the scheduler runs the pipeline once at startup and every 15 minutes after.

### One-shot debugging

```bash
python3 -m scripts.one_shot --station WSSS                # today's edge for Changi
python3 -m scripts.one_shot --station WSSS --force-alert  # also pings Telegram
```

### Calibration backfill

```bash
python3 -m scripts.backfill_obs --station WSSS --days 365
```

## Deploy to Fly.io

```bash
fly launch --no-deploy --copy-config
fly volumes create data --size 1 --region sin
fly secrets set TELEGRAM_BOT_TOKEN=xxx TELEGRAM_CHAT_ID=yyy BANKROLL_USD=500
fly deploy
fly status
```

## Use it from your iPhone

1. Open the public Fly URL in Safari.
2. Share → **Add to Home Screen**. The icon launches the dashboard fullscreen.
3. Talk to your Telegram bot once (`/start`) so it has your chat id, then push alerts will arrive when edges appear.
4. Tapping a bin's "Bet on Polymarket" button opens the Polymarket app (or Safari fallback) at the matching event.

## Configuration

Settings come from `.env` (or Fly secrets). See `.env.example` for the full list — most-tuned ones:

| Var | Default | Meaning |
|---|---|---|
| `BANKROLL_USD` | 500 | Total stake budget |
| `KELLY_FRACTION` | 0.25 | Fractional-Kelly multiplier |
| `EDGE_THRESHOLD` | 0.03 | Minimum post-fee edge (probability points) |
| `MIN_DEPTH_USD` | 50 | Skip bins with thinner book |
| `STATIONS` | `WSSS,KLGA` | ICAO codes to watch |

## Architecture

Single Python container on Fly.io running:
- FastAPI for the dashboard, JSON API, PWA manifest and service worker.
- APScheduler running pipeline (every 15 min) and METAR (every 30 min) jobs in-process.
- python-telegram-bot polling for `/start`, `/top` and pushing edge alerts.
- SQLite on a Fly volume for the forecast log, market snapshots, recommendations and alert dedup.

## Tests

```bash
python3 -m pytest
```

Covers Kelly math, fee-adjusted edge filtering, bankroll caps, KDE-CDF monotonicity, NHGR fitting, and Polymarket bin-label parsing.

## Phase 2 (not in this code)

- Auto-trading via `py-clob-client` once paper-trading shows realised edge ≥ market for ≥14 days.
- NBM/HRRR augmentation for US stations.
- Hurricanes / monthly-average / snowfall pipelines.
