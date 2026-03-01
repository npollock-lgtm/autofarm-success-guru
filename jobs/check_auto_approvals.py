"""
Check Auto-Approvals — runs every 30 minutes.

Checks for reviews that have exceeded their auto-approval timeout
and automatically approves them.
"""

import asyncio
import logging

from modules.infrastructure.logging_config import setup_logging
from modules.review_gate.approval_tracker import ApprovalTracker
from database.db import Database

logger = logging.getLogger("autofarm.jobs.check_auto_approvals")


async def main() -> None:
    """Auto-approve reviews past their expiry threshold.

    Side Effects
    ------------
    Updates expired pending reviews to 'approved' status.
    Creates publish jobs for auto-approved videos.
    """
    setup_logging()
    logger.info("Starting check_auto_approvals job")

    db = Database()
    await db.initialize()

    try:
        tracker = ApprovalTracker(db=db)
        count = await tracker.process_auto_approvals()
        if count:
            logger.info("Auto-approved %d reviews", count)
    except Exception as exc:
        logger.error("check_auto_approvals error: %s", exc, exc_info=True)
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
