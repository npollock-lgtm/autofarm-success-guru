"""
Publish Due — runs every 5 minutes.

Checks for scheduled publish jobs that are due and publishes them
through the appropriate platform publisher.
"""

import asyncio
import logging

from modules.infrastructure.logging_config import setup_logging
from modules.publish_engine.publisher import Publisher
from database.db import Database

logger = logging.getLogger("autofarm.jobs.publish_due")


async def main() -> None:
    """Publish all videos that are due for posting.

    Side Effects
    ------------
    Publishes videos to their scheduled platforms.
    Updates publish_jobs status.
    Posts first comments after successful publish.
    """
    setup_logging()
    logger.info("Starting publish_due job")

    db = Database()
    await db.initialize()

    try:
        publisher = Publisher(db=db)
        result = await publisher.publish_due_videos()
        logger.info(
            "Publish results: %d published, %d failed, %d skipped",
            result.get("published", 0),
            result.get("failed", 0),
            result.get("skipped", 0),
        )
    except Exception as exc:
        logger.error("publish_due error: %s", exc, exc_info=True)
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
