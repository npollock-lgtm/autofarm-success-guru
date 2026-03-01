"""
Content Queue — manages the full content pre-production pipeline.

Target: always maintain ``QUEUE_TARGET_DAYS_AHEAD`` days of ready-to-publish
content per brand per platform.

After review approval, videos are added to the queue with a scheduled publish
time.  The publisher job polls ``get_next_ready()`` every 5 minutes.

Stale content (> 14 days old) is automatically flushed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("autofarm.queue.content_queue")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STALE_DAYS = 14  # Content older than this is flushed from queue


# ---------------------------------------------------------------------------
# ContentQueue
# ---------------------------------------------------------------------------


class ContentQueue:
    """Manage the publish queue across all brands and platforms.

    Parameters
    ----------
    db:
        Database helper instance.
    schedule_config:
        Optional schedule configuration dict mapping
        ``{brand_id: {platform: posts_per_day}}``.
    """

    QUEUE_TARGET_DAYS_AHEAD = 3

    def __init__(
        self,
        db: Any,
        schedule_config: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.db = db
        self.schedule_config = schedule_config or {}

    # ------------------------------------------------------------------
    # Queue depth
    # ------------------------------------------------------------------

    async def get_queue_depth(self, brand_id: str, platform: str) -> int:
        """Return the number of approved, ready-to-publish videos in the queue.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        platform:
            Target platform.

        Returns
        -------
        int
            Count of queued videos.
        """
        row = await self.db.fetch_one(
            """
            SELECT COUNT(*) AS cnt FROM content_queue
            WHERE brand_id = ? AND platform = ? AND status = 'waiting'
            """,
            (brand_id, platform),
        )
        return row["cnt"] if row else 0

    # ------------------------------------------------------------------
    # Needs-more-content check
    # ------------------------------------------------------------------

    async def needs_more_content(self, brand_id: str) -> bool:
        """Return ``True`` if any platform for this brand is below target.

        Parameters
        ----------
        brand_id:
            Brand identifier.

        Returns
        -------
        bool
            ``True`` when more content should be generated.
        """
        brand_sched = self.schedule_config.get(brand_id, {})
        platforms = brand_sched.keys() if brand_sched else [
            "tiktok", "instagram", "youtube"
        ]

        for platform in platforms:
            posts_per_day = brand_sched.get(platform, {}).get("posts_per_day", 1)
            target = posts_per_day * self.QUEUE_TARGET_DAYS_AHEAD
            depth = await self.get_queue_depth(brand_id, platform)
            if depth < target:
                logger.info(
                    "Brand %s needs content for %s: %d queued / %d target",
                    brand_id, platform, depth, target,
                )
                return True
        return False

    # ------------------------------------------------------------------
    # Add to queue
    # ------------------------------------------------------------------

    async def add_to_queue(
        self,
        video_id: int,
        brand_id: str,
        platforms: Optional[List[str]] = None,
    ) -> bool:
        """Add a reviewed / approved video to the publish queue.

        Parameters
        ----------
        video_id:
            Primary key in the ``videos`` table.
        brand_id:
            Brand identifier.
        platforms:
            Target platforms; defaults to all configured.

        Returns
        -------
        bool
            ``True`` on success.

        Side Effects
        ------------
        Inserts row(s) into ``content_queue`` with calculated schedule times.
        """
        if platforms is None:
            brand_sched = self.schedule_config.get(brand_id, {})
            platforms = list(brand_sched.keys()) if brand_sched else [
                "tiktok", "instagram", "youtube"
            ]

        success = True
        for platform in platforms:
            scheduled_time = await self._calculate_next_slot(brand_id, platform)
            try:
                await self.db.execute(
                    """
                    INSERT OR IGNORE INTO content_queue
                        (video_id, brand_id, platform, status, scheduled_for)
                    VALUES (?, ?, ?, 'waiting', ?)
                    """,
                    (video_id, brand_id, platform, scheduled_time.isoformat()),
                )
                logger.info(
                    "Queued video %d for %s/%s at %s",
                    video_id, brand_id, platform, scheduled_time.isoformat(),
                )
            except Exception as exc:
                logger.error("Failed to queue video %d: %s", video_id, exc)
                success = False

        return success

    # ------------------------------------------------------------------
    # Get next ready
    # ------------------------------------------------------------------

    async def get_next_ready(
        self, brand_id: str, platform: str
    ) -> Optional[Dict[str, Any]]:
        """Return the next video ready for publish on this brand × platform.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        platform:
            Platform name.

        Returns
        -------
        Optional[Dict[str, Any]]
            Queue entry dict or ``None`` if nothing is due.
        """
        now = datetime.now(timezone.utc).isoformat()
        row = await self.db.fetch_one(
            """
            SELECT id, video_id, brand_id, platform, status, scheduled_for, added_at
            FROM content_queue
            WHERE brand_id = ? AND platform = ? AND status = 'waiting'
                  AND scheduled_for <= ?
            ORDER BY scheduled_for ASC
            LIMIT 1
            """,
            (brand_id, platform, now),
        )
        if row:
            return dict(row)
        return None

    async def mark_published(self, queue_id: int) -> None:
        """Mark a queue entry as published.

        Parameters
        ----------
        queue_id:
            Primary key in ``content_queue``.

        Side Effects
        ------------
        Updates the row status to ``'published'``.
        """
        await self.db.execute(
            "UPDATE content_queue SET status = 'published' WHERE id = ?",
            (queue_id,),
        )

    async def mark_failed(self, queue_id: int, reason: str = "") -> None:
        """Mark a queue entry as failed.

        Parameters
        ----------
        queue_id:
            Primary key in ``content_queue``.
        reason:
            Failure description.

        Side Effects
        ------------
        Updates the row status to ``'failed'``.
        """
        await self.db.execute(
            "UPDATE content_queue SET status = 'failed' WHERE id = ?",
            (queue_id,),
        )
        logger.warning("Queue entry %d marked failed: %s", queue_id, reason)

    # ------------------------------------------------------------------
    # Queue status
    # ------------------------------------------------------------------

    async def get_queue_status(self) -> Dict[str, Dict[str, Dict[str, Any]]]:
        """Return a status overview: ``{brand_id: {platform: {queued, days_ahead, next_post}}}``.

        Returns
        -------
        Dict[str, Dict[str, Dict[str, Any]]]
        """
        rows = await self.db.fetch_all(
            """
            SELECT brand_id, platform,
                   COUNT(*) AS queued,
                   MIN(scheduled_for) AS next_post
            FROM content_queue
            WHERE status = 'waiting'
            GROUP BY brand_id, platform
            """
        )
        status: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for row in rows:
            bid = row["brand_id"]
            plat = row["platform"]
            queued = row["queued"]
            next_post = row["next_post"]

            # Calculate days ahead
            brand_sched = self.schedule_config.get(bid, {})
            posts_per_day = brand_sched.get(plat, {}).get("posts_per_day", 1)
            days_ahead = round(queued / max(posts_per_day, 1), 1)

            if bid not in status:
                status[bid] = {}
            status[bid][plat] = {
                "queued": queued,
                "days_ahead": days_ahead,
                "next_post": next_post,
            }

        return status

    # ------------------------------------------------------------------
    # Housekeeping
    # ------------------------------------------------------------------

    async def flush_expired(self) -> int:
        """Remove videos from the queue that are older than ``STALE_DAYS``.

        Returns
        -------
        int
            Number of rows deleted.

        Side Effects
        ------------
        Deletes stale rows from ``content_queue``.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=STALE_DAYS)).isoformat()
        result = await self.db.execute(
            """
            DELETE FROM content_queue
            WHERE status = 'waiting' AND added_at < ?
            """,
            (cutoff,),
        )
        count = result if isinstance(result, int) else 0
        if count:
            logger.info("Flushed %d stale queue entries", count)
        return count

    # ------------------------------------------------------------------
    # Scheduling helper
    # ------------------------------------------------------------------

    async def _calculate_next_slot(
        self, brand_id: str, platform: str
    ) -> datetime:
        """Calculate the next available publish slot for brand × platform.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        platform:
            Platform name.

        Returns
        -------
        datetime
            UTC datetime for the next slot.
        """
        # Find the latest scheduled time already in queue
        row = await self.db.fetch_one(
            """
            SELECT MAX(scheduled_for) AS latest
            FROM content_queue
            WHERE brand_id = ? AND platform = ? AND status = 'waiting'
            """,
            (brand_id, platform),
        )

        brand_sched = self.schedule_config.get(brand_id, {})
        platform_conf = brand_sched.get(platform, {})
        posts_per_day = platform_conf.get("posts_per_day", 1)
        interval_hours = 24.0 / max(posts_per_day, 1)

        if row and row["latest"]:
            try:
                latest = datetime.fromisoformat(row["latest"])
                if latest.tzinfo is None:
                    latest = latest.replace(tzinfo=timezone.utc)
                next_slot = latest + timedelta(hours=interval_hours)
            except (ValueError, TypeError):
                next_slot = datetime.now(timezone.utc) + timedelta(hours=1)
        else:
            # First in queue — schedule for next hour
            next_slot = datetime.now(timezone.utc) + timedelta(hours=1)

        # Ensure slot is in the future
        now = datetime.now(timezone.utc)
        if next_slot <= now:
            next_slot = now + timedelta(minutes=30)

        return next_slot
