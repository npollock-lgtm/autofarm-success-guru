"""
Reset API Quotas — runs daily at 00:01 UTC.

Resets platform-specific API quotas (especially YouTube's 10,000
daily quota units) at the start of each day.
"""

import asyncio
import logging

from modules.infrastructure.logging_config import setup_logging
from database.db import Database

logger = logging.getLogger("autofarm.jobs.reset_api_quotas")


async def main() -> None:
    """Reset API quota counters for all platforms.

    Side Effects
    ------------
    Resets ``rate_limits`` rows with ``window_type='api_daily'``.
    Specifically handles YouTube's 10,000 daily quota reset.
    """
    setup_logging()
    logger.info("Starting reset_api_quotas job")

    db = Database()
    await db.initialize()

    try:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()

        # Reset all API daily quotas
        await db.execute(
            """
            UPDATE rate_limits
            SET count = 0, units = 0, window_start = ?, window_end = NULL
            WHERE window_type IN ('api_daily', 'quota_daily')
            """,
            (now,),
        )

        # Reset hourly windows that have expired
        await db.execute(
            """
            UPDATE rate_limits
            SET count = 0, units = 0, window_start = ?
            WHERE window_type = 'hourly' AND window_end < ?
            """,
            (now, now),
        )

        logger.info("Reset API quotas at %s", now)

    except Exception as exc:
        logger.error("reset_api_quotas error: %s", exc, exc_info=True)
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
