"""
Maintain Backgrounds — runs weekly on Monday at 02:00 UTC.

Refreshes the background video library: downloads new clips from Pexels
and Pixabay, removes expired entries, and verifies file integrity.
"""

import asyncio
import logging

from modules.infrastructure.logging_config import setup_logging
from modules.content_forge.background_library import BackgroundManager
from database.db import Database

logger = logging.getLogger("autofarm.jobs.maintain_backgrounds")


async def main() -> None:
    """Maintain the background video library.

    Side Effects
    ------------
    Downloads new backgrounds, removes expired ones, updates
    ``background_library`` table.
    """
    setup_logging()
    logger.info("Starting maintain_backgrounds job")

    db = Database()
    await db.initialize()

    try:
        manager = BackgroundManager(db=db)
        result = await manager.maintain_library()
        logger.info("Background maintenance: %s", result)
    except Exception as exc:
        logger.error("maintain_backgrounds error: %s", exc, exc_info=True)
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
