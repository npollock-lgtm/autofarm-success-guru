"""
Reset Daily Counts — runs at midnight UTC (00:00).

Resets daily rate-limit counters for all brands and platforms so the
new day starts with fresh quotas.
"""

import asyncio
import logging

from modules.infrastructure.logging_config import setup_logging
from database.db import Database

logger = logging.getLogger("autofarm.jobs.reset_daily_counts")


async def main() -> None:
    """Reset all daily rate-limit counters.

    Side Effects
    ------------
    Updates ``rate_limits`` rows with ``window_type='daily'`` to zero.
    """
    setup_logging()
    logger.info("Starting reset_daily_counts job")

    db = Database()
    await db.initialize()

    try:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()

        result = await db.execute(
            """
            UPDATE rate_limits
            SET count = 0, units = 0, window_start = ?, window_end = NULL
            WHERE window_type = 'daily'
            """,
            (now,),
        )
        logger.info("Reset daily rate-limit counters at %s", now)

    except Exception as exc:
        logger.error("reset_daily_counts error: %s", exc, exc_info=True)
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
