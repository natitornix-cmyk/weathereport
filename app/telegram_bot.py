from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from .config import settings

if TYPE_CHECKING:
    from .pricing import Recommendation

log = logging.getLogger("weathereport.telegram")

_app = None
_app_lock = asyncio.Lock()


async def _get_app():
    global _app
    if not settings.telegram_bot_token:
        return None
    async with _app_lock:
        if _app is None:
            try:
                from telegram.ext import ApplicationBuilder, CommandHandler
            except ImportError:
                log.warning("python-telegram-bot not installed; alerts disabled")
                return None
            app = ApplicationBuilder().token(settings.telegram_bot_token).build()
            app.add_handler(CommandHandler("start", _cmd_start))
            app.add_handler(CommandHandler("top", _cmd_top))
            await app.initialize()
            _app = app
    return _app


async def start_bot():
    app = await _get_app()
    if app is None:
        log.info("telegram disabled (no token)")
        return
    await app.start()
    if app.updater is not None:
        await app.updater.start_polling()
    log.info("telegram bot started")


async def stop_bot():
    global _app
    if _app is None:
        return
    try:
        if _app.updater is not None:
            await _app.updater.stop()
        await _app.stop()
        await _app.shutdown()
    except Exception as e:  # noqa: BLE001
        log.warning("telegram shutdown error: %s", e)
    _app = None


async def _cmd_start(update, context):
    await update.message.reply_text(
        "weathereport bot online.\n"
        "/top — show ranked edge recommendations\n"
        "Dashboard: open the home-screen icon."
    )


async def _cmd_top(update, context):
    msg = await _format_top()
    await update.message.reply_markdown(msg, disable_web_page_preview=True)


async def _format_top() -> str:
    from datetime import date
    from sqlmodel import Session, desc, select

    from .db import Recommendation as RecRow, engine

    with Session(engine()) as ses:
        rows = ses.exec(
            select(RecRow).order_by(desc(RecRow.created_at)).limit(30)
        ).all()
    if not rows:
        return "_No recommendations yet._"
    seen = set()
    lines = ["*Top edges*"]
    for r in rows:
        key = (r.event_id, r.bin_label)
        if key in seen:
            continue
        seen.add(key)
        if len(lines) > 7:
            break
        lines.append(
            f"`{r.station}` {r.target_date} {r.bin_label} — "
            f"p={r.our_prob:.2f} ask={r.market_ask:.2f} edge={r.edge:.2f} "
            f"size=${r.stake_usd:.0f}\n[bet]({r.polymarket_url})"
        )
    return "\n".join(lines)


async def push_alert(rec: "Recommendation") -> None:
    app = await _get_app()
    if app is None or not settings.telegram_chat_id:
        return
    txt = (
        f"*Edge alert*\n"
        f"`{rec.event.station_code}` {rec.event.target_date} *{rec.bin.label}*\n"
        f"our p = {rec.our_prob:.2f}, ask = {rec.market_ask:.2f}, "
        f"edge = {rec.edge:.2f}\nstake = ${rec.stake_usd:.0f}\n"
        f"[Open on Polymarket]({rec.polymarket_url})"
    )
    try:
        await app.bot.send_message(
            chat_id=settings.telegram_chat_id,
            text=txt,
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("telegram send failed: %s", e)
