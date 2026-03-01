"""
Pull Analytics — runs daily at 03:00 UTC.

Pulls engagement metrics from all platforms for recently published videos,
scores them with CPS, and triggers hook weight optimization.
"""

import asyncio
import logging

from modules.infrastructure.logging_config import setup_logging
from modules.feedback_loop.analytics_puller import AnalyticsPuller
from modules.feedback_loop.scorer import EngagementScorer
from modules.feedback_loop.hook_optimizer import HookOptimizer
from modules.compliance.rate_limit_manager import RateLimitManager
from database.credential_manager import CredentialManager
from modules.network.ip_router import BrandIPRouter
from database.db import Database

logger = logging.getLogger("autofarm.jobs.pull_analytics")


async def main() -> None:
    """Pull analytics and update performance scores.

    Side Effects
    ------------
    Inserts analytics rows, updates CPS scores,
    recalculates hook weights.
    """
    setup_logging()
    logger.info("Starting pull_analytics job")

    db = Database()
    await db.initialize()

    try:
        rate_limiter = RateLimitManager(db=db)
        cred_manager = CredentialManager(db=db)
        ip_router = BrandIPRouter(db=db)

        puller = AnalyticsPuller(
            db=db, rate_limiter=rate_limiter,
            credential_manager=cred_manager, ip_router=ip_router,
        )
        scorer = EngagementScorer(db=db)
        optimizer = HookOptimizer(db=db, scorer=scorer)

        # 1. Pull metrics from all platforms
        pull_result = await puller.pull_all_analytics()
        logger.info(
            "Analytics pull: %d success, %d failed",
            pull_result.get("successful_pulls", 0),
            pull_result.get("failed_pulls", 0),
        )

        # 2. Score unscored videos
        scored = await scorer.score_recent_unscored(lookback_days=14)
        logger.info("Scored %d videos", len(scored))

        # 3. Optimize hook weights
        opt_result = await optimizer.optimise_all()
        logger.info("Hook optimization: %s", opt_result)

    except Exception as exc:
        logger.error("pull_analytics error: %s", exc, exc_info=True)
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
