"""
Scan and Generate — runs every 2 hours.

1. Checks ``ResourceScheduler`` for RAM/CPU availability.
2. Calls trend scanners to find new topics.
3. Checks ``ContentQueue`` depth per brand.
4. Generates scripts, videos, and queues them for review.
"""

import asyncio
import logging
import sys

from modules.infrastructure.logging_config import setup_logging
from modules.infrastructure.resource_scheduler import ResourceScheduler
from modules.infrastructure.shutdown_handler import ShutdownHandler
from modules.infrastructure.job_state_machine import JobStateMachine
from modules.trend_scanner.scanner import TrendScanner
from modules.ai_brain.brain import ContentBrain
from modules.content_forge.forge import ContentForge
from modules.queue.content_queue import ContentQueue
from modules.brand.quality_gate import QualityGate
from modules.review_gate.gate import ReviewGate
from database.db import Database

logger = logging.getLogger("autofarm.jobs.scan_and_generate")


async def main() -> None:
    """Main entry point for the scan-and-generate pipeline.

    Side Effects
    ------------
    - Scans trends across all sources.
    - Generates scripts for brands with low queue depth.
    - Assembles videos and submits to review gate.
    """
    setup_logging()
    logger.info("Starting scan_and_generate job")

    db = Database()
    await db.initialize()

    shutdown = ShutdownHandler()
    resource_scheduler = ResourceScheduler(db=db)
    state_machine = JobStateMachine(db=db)

    # Resource gate — skip if system is under pressure
    if not await resource_scheduler.can_run_heavy_job():
        logger.warning("Insufficient resources — skipping generation cycle")
        await db.close()
        return

    try:
        scanner = TrendScanner(db=db)
        brain = ContentBrain(db=db)
        forge = ContentForge(db=db)
        queue = ContentQueue(db=db)
        quality_gate = QualityGate(db=db)
        review_gate = ReviewGate(db=db)

        # 1. Scan trends
        logger.info("Scanning trends...")
        trends = await scanner.scan_all()
        logger.info("Found %d trends", len(trends))

        # 2. Get brands that need content
        brands = await db.fetch_all("SELECT id FROM brands")

        for brand in brands:
            if shutdown.should_shutdown:
                logger.info("Shutdown requested — stopping generation")
                break

            brand_id = brand["id"]

            # Check queue depth
            if not await queue.needs_more_content(brand_id):
                logger.info("Queue full for %s — skipping", brand_id)
                continue

            # 3. Generate script
            job_id = await state_machine.create_job(
                "content_generation", brand_id
            )
            await state_machine.transition(job_id, "generating_script")

            try:
                script = await brain.generate_content(brand_id, trends)
                if not script:
                    await state_machine.transition(job_id, "failed")
                    continue

                # 4. Quality gate
                await state_machine.transition(job_id, "quality_check")
                qg_result = await quality_gate.check(script["id"], brand_id)
                if not qg_result.get("passed"):
                    logger.info(
                        "Quality gate rejected script %d for %s",
                        script["id"], brand_id,
                    )
                    await state_machine.transition(job_id, "rejected")
                    continue

                # 5. Forge video
                await state_machine.transition(job_id, "forging_video")
                video = await forge.create_video(script["id"], brand_id)
                if not video:
                    await state_machine.transition(job_id, "failed")
                    continue

                # 6. Submit for review
                await state_machine.transition(job_id, "pending_review")
                await review_gate.submit_for_review(
                    video["id"], brand_id
                )

                # 7. Add to queue
                await queue.add_to_queue(brand_id, script["id"], video["id"])
                await state_machine.transition(job_id, "queued")

                logger.info(
                    "Generated and queued content for %s (script=%d, video=%d)",
                    brand_id, script["id"], video["id"],
                )

            except Exception as exc:
                logger.error(
                    "Generation failed for %s: %s", brand_id, exc
                )
                await state_machine.transition(job_id, "failed")

    except Exception as exc:
        logger.error("scan_and_generate error: %s", exc, exc_info=True)
    finally:
        await db.close()

    logger.info("scan_and_generate job complete")


if __name__ == "__main__":
    asyncio.run(main())
