"""
Refresh User Agents — runs monthly on the 1st at 03:00 UTC.

Generates fresh, realistic user-agent strings for each brand persona
to maintain fingerprint consistency and avoid detection.
"""

import asyncio
import logging

from modules.infrastructure.logging_config import setup_logging
from modules.network.ua_generator import UAGenerator
from database.db import Database

logger = logging.getLogger("autofarm.jobs.refresh_user_agents")


async def main() -> None:
    """Regenerate user-agent strings for all brands.

    Side Effects
    ------------
    Updates ``user_agents`` table with fresh UA strings per brand.
    """
    setup_logging()
    logger.info("Starting refresh_user_agents job")

    db = Database()
    await db.initialize()

    try:
        generator = UAGenerator(db=db)
        result = await generator.refresh_all_user_agents()
        logger.info("User agent refresh: %s", result)

    except Exception as exc:
        logger.error("refresh_user_agents error: %s", exc, exc_info=True)
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
