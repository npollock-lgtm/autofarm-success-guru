"""
Send Daily Digest — runs daily at 08:00 UTC.

Sends a daily summary via both Telegram (primary) and email (fallback).
"""

import asyncio
import logging

from modules.infrastructure.logging_config import setup_logging
from modules.notifications.telegram_bot import TelegramNotifier
from modules.notifications.email_notifier import EmailNotifier
from database.db import Database

logger = logging.getLogger("autofarm.jobs.send_daily_digest")


async def main() -> None:
    """Send daily digest via Telegram and email.

    Side Effects
    ------------
    Sends notification messages summarising yesterday's activity.
    """
    setup_logging()
    logger.info("Starting send_daily_digest job")

    db = Database()
    await db.initialize()

    try:
        # Telegram (primary)
        telegram = TelegramNotifier(db=db)
        tg_ok = await telegram.send_daily_digest()
        if tg_ok:
            logger.info("Daily digest sent via Telegram")
        else:
            logger.warning("Telegram digest failed — trying email fallback")

        # Email (fallback or additional)
        email = EmailNotifier(db=db)
        email_ok = await email.send_daily_digest()
        if email_ok:
            logger.info("Daily digest sent via email")
        elif not tg_ok:
            logger.error("Both Telegram and email digest failed")

    except Exception as exc:
        logger.error("send_daily_digest error: %s", exc, exc_info=True)
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
