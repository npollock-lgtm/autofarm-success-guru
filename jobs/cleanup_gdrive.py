"""
Cleanup Google Drive — runs daily at 05:00 UTC.

Removes review files older than 14 days from Google Drive to stay
within the 15 GB free quota. Only runs if GDRIVE_ENABLED=true.
"""

import asyncio
import logging
import os

from modules.infrastructure.logging_config import setup_logging
from database.db import Database

logger = logging.getLogger("autofarm.jobs.cleanup_gdrive")


async def main() -> None:
    """Clean up expired Google Drive review files.

    Side Effects
    ------------
    Deletes files older than 14 days from Google Drive.
    Updates ``gdrive_review_files`` table.
    """
    setup_logging()
    logger.info("Starting cleanup_gdrive job")

    if os.getenv("GDRIVE_ENABLED", "").lower() != "true":
        logger.info("Google Drive not enabled — skipping")
        return

    db = Database()
    await db.initialize()

    try:
        from modules.review_gate.gdrive_uploader import GDriveVideoUploader

        uploader = GDriveVideoUploader(db=db)
        result = await uploader.cleanup_expired_reviews()
        logger.info("Google Drive cleanup: %s", result)

    except Exception as exc:
        logger.error("cleanup_gdrive error: %s", exc, exc_info=True)
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
