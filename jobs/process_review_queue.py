"""
Process Review Queue — runs every 15 minutes.

Sends pending reviews via Telegram (primary) or email (fallback).
"""

import asyncio
import logging

from modules.infrastructure.logging_config import setup_logging
from modules.review_gate.gate import ReviewGate
from database.db import Database

logger = logging.getLogger("autofarm.jobs.process_review_queue")


async def main() -> None:
    """Process all pending reviews that haven't been sent yet.

    Side Effects
    ------------
    Sends review packages via Telegram or email.
    Updates review records with sent status.
    """
    setup_logging()
    logger.info("Starting process_review_queue job")

    db = Database()
    await db.initialize()

    try:
        gate = ReviewGate(db=db)
        result = await gate.process_pending_reviews()
        logger.info(
            "Processed reviews: %d sent, %d failed",
            result.get("sent", 0), result.get("failed", 0),
        )
    except Exception as exc:
        logger.error("process_review_queue error: %s", exc, exc_info=True)
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
