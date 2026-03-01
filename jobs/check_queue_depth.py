"""
Check Queue Depth — runs hourly.

Monitors content queue depth per brand and sends alerts if any brand
is running low on ready content.
"""

import asyncio
import logging

from modules.infrastructure.logging_config import setup_logging
from modules.queue.content_queue import ContentQueue
from modules.notifications.telegram_bot import TelegramNotifier
from database.db import Database

logger = logging.getLogger("autofarm.jobs.check_queue_depth")

# Minimum queue depth before alerting
MIN_QUEUE_DEPTH = 2


async def main() -> None:
    """Check content queue depth for all brands.

    Side Effects
    ------------
    Sends Telegram alert if any brand has fewer than MIN_QUEUE_DEPTH
    items ready in the queue.
    """
    setup_logging()
    logger.info("Starting check_queue_depth job")

    db = Database()
    await db.initialize()

    try:
        queue = ContentQueue(db=db)
        notifier = TelegramNotifier(db=db)

        brands = await db.fetch_all("SELECT id FROM brands")
        low_brands = []

        for brand in brands:
            brand_id = brand["id"]
            depth = await queue.get_queue_depth(brand_id)

            if depth < MIN_QUEUE_DEPTH:
                low_brands.append(f"{brand_id}: {depth} items")

        if low_brands:
            message = (
                "\u26a0\ufe0f <b>Low Queue Alert</b>\n\n"
                + "\n".join(low_brands)
                + "\n\nContent generation may be needed."
            )
            await notifier.send_message(message)
            logger.warning("Low queue depth: %s", low_brands)
        else:
            logger.info("Queue depths OK for all brands")

    except Exception as exc:
        logger.error("check_queue_depth error: %s", exc, exc_info=True)
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
