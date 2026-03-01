"""
Reoptimise Schedule — runs weekly on Monday at 04:00 UTC.

Runs the full model updater which:
- Updates posting window rankings based on performance data.
- Adjusts content pillar weights.
- Refreshes cached LLM templates with top-performing patterns.
- Updates hook performance weights.
"""

import asyncio
import logging

from modules.infrastructure.logging_config import setup_logging
from modules.feedback_loop.model_updater import ModelUpdater
from modules.feedback_loop.scorer import EngagementScorer
from modules.feedback_loop.hook_optimizer import HookOptimizer
from database.db import Database

logger = logging.getLogger("autofarm.jobs.reoptimise_schedule")


async def main() -> None:
    """Run full model update and schedule reoptimization.

    Side Effects
    ------------
    Updates schedule rankings, pillar weights, cached templates,
    and hook performance weights.
    """
    setup_logging()
    logger.info("Starting reoptimise_schedule job")

    db = Database()
    await db.initialize()

    try:
        scorer = EngagementScorer(db=db)
        optimizer = HookOptimizer(db=db, scorer=scorer)
        updater = ModelUpdater(db=db, scorer=scorer, hook_optimizer=optimizer)

        result = await updater.run_full_update()
        logger.info("Model update complete: %s", result)

    except Exception as exc:
        logger.error("reoptimise_schedule error: %s", exc, exc_info=True)
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
