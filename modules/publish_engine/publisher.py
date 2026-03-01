"""
Publisher — main orchestrator for the publishing pipeline.

Reads due videos from the content queue, publishes via the appropriate
platform publisher, handles retries, and updates database state.

Called every 5 minutes by ``jobs/publish_due.py``.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("autofarm.publish_engine.publisher")

MAX_RETRIES = 5


class Publisher:
    """Orchestrate the publishing pipeline across all platforms.

    Parameters
    ----------
    db:
        Database helper instance.
    rate_limiter:
        ``RateLimitManager`` for compliance checks.
    scheduler:
        ``SmartScheduler`` for rescheduling on failure.
    ip_router:
        ``BrandIPRouter`` for session management.
    credential_manager:
        ``CredentialManager`` for token access.
    notifier:
        Optional notification helper (Telegram bot) for failure alerts.
    """

    def __init__(
        self,
        db: Any,
        rate_limiter: Any,
        scheduler: Any,
        ip_router: Any,
        credential_manager: Any,
        notifier: Optional[Any] = None,
    ) -> None:
        self.db = db
        self.rate_limiter = rate_limiter
        self.scheduler = scheduler
        self.ip_router = ip_router
        self.credential_manager = credential_manager
        self.notifier = notifier

    # ------------------------------------------------------------------
    # Primary API
    # ------------------------------------------------------------------

    async def publish_due_now(self) -> List[Dict[str, Any]]:
        """Find and publish all videos that are due.

        Returns
        -------
        List[Dict[str, Any]]
            Publish results for each video × platform.

        Side Effects
        ------------
        * Updates ``content_queue`` status.
        * Inserts rows into ``published_videos``.
        * Sends failure alerts if retries exhausted.
        """
        due = await self._get_due_videos()
        results: List[Dict[str, Any]] = []

        for record in due:
            result = await self._publish_single(record)
            results.append(result)

        if results:
            logger.info(
                "Publish run complete: %d attempted, %d succeeded",
                len(results),
                sum(1 for r in results if r.get("success")),
            )
        return results

    # ------------------------------------------------------------------
    # Query due videos
    # ------------------------------------------------------------------

    async def _get_due_videos(self) -> List[Dict[str, Any]]:
        """Query the content queue for videos scheduled now or earlier.

        Returns
        -------
        List[Dict[str, Any]]
        """
        now = datetime.now(timezone.utc).isoformat()
        rows = await self.db.fetch_all(
            """
            SELECT cq.id AS queue_id, cq.video_id, cq.brand_id, cq.platform,
                   cq.scheduled_for
            FROM content_queue cq
            WHERE cq.status = 'waiting' AND cq.scheduled_for <= ?
            ORDER BY cq.scheduled_for ASC
            """,
            (now,),
        )
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Publish single
    # ------------------------------------------------------------------

    async def _publish_single(
        self, record: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Publish a single video to a single platform.

        Parameters
        ----------
        record:
            Queue record dict with ``queue_id``, ``video_id``, ``brand_id``, ``platform``.

        Returns
        -------
        Dict[str, Any]
        """
        brand_id = record["brand_id"]
        platform = record["platform"]
        video_id = record["video_id"]
        queue_id = record["queue_id"]

        # Load video data
        video = await self.db.fetch_one(
            "SELECT * FROM videos WHERE id = ?", (video_id,)
        )
        if not video:
            logger.error("Video %d not found — skipping", video_id)
            await self.db.execute(
                "UPDATE content_queue SET status = 'failed' WHERE id = ?",
                (queue_id,),
            )
            return {"video_id": video_id, "platform": platform, "success": False, "error": "Video not found"}

        publish_data = self._build_publish_data(dict(video), brand_id, platform)

        # Get publisher
        publisher = self._get_publisher(brand_id, platform)
        if not publisher:
            return {"video_id": video_id, "platform": platform, "success": False, "error": f"No publisher for {platform}"}

        try:
            result = await publisher.publish(video_id, publish_data)

            if result.get("success"):
                await self._mark_published(
                    queue_id, video_id, brand_id, platform,
                    result.get("platform_post_id", ""),
                    result.get("video_url", ""),
                )
                return {
                    "video_id": video_id,
                    "platform": platform,
                    "success": True,
                    "platform_post_id": result.get("platform_post_id"),
                    "video_url": result.get("video_url"),
                }
            else:
                await self._handle_failure(
                    queue_id, video_id, brand_id, platform,
                    result.get("error", "Unknown error"),
                )
                return {
                    "video_id": video_id,
                    "platform": platform,
                    "success": False,
                    "error": result.get("error"),
                }
        except Exception as exc:
            logger.error("Publish exception: %s", exc)
            await self._handle_failure(
                queue_id, video_id, brand_id, platform, str(exc)
            )
            return {"video_id": video_id, "platform": platform, "success": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Publisher factory
    # ------------------------------------------------------------------

    def _get_publisher(self, brand_id: str, platform: str) -> Optional[Any]:
        """Instantiate the appropriate platform publisher.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        platform:
            Platform name.

        Returns
        -------
        Optional[BasePlatformPublisher]
        """
        from modules.publish_engine.tiktok import TikTokPublisher
        from modules.publish_engine.instagram import InstagramPublisher
        from modules.publish_engine.facebook import FacebookPublisher
        from modules.publish_engine.youtube import YouTubePublisher
        from modules.publish_engine.snapchat import SnapchatPublisher

        publishers = {
            "tiktok": TikTokPublisher,
            "instagram": InstagramPublisher,
            "facebook": FacebookPublisher,
            "youtube": YouTubePublisher,
            "snapchat": SnapchatPublisher,
        }
        cls = publishers.get(platform)
        if cls is None:
            logger.error("Unknown platform: %s", platform)
            return None

        return cls(
            brand_id=brand_id,
            platform=platform,
            ip_router=self.ip_router,
            rate_limiter=self.rate_limiter,
            credential_manager=self.credential_manager,
            db=self.db,
        )

    # ------------------------------------------------------------------
    # Database updates
    # ------------------------------------------------------------------

    async def _mark_published(
        self,
        queue_id: int,
        video_id: int,
        brand_id: str,
        platform: str,
        platform_post_id: str,
        post_url: str,
    ) -> None:
        """Update DB after successful publish.

        Side Effects
        ------------
        * Marks content_queue entry as ``'published'``.
        * Inserts/updates ``published_videos`` row.
        """
        now = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            "UPDATE content_queue SET status = 'published' WHERE id = ?",
            (queue_id,),
        )
        await self.db.execute(
            """
            INSERT INTO published_videos
                (video_id, brand_id, platform, status, platform_post_id,
                 post_url, published_at, created_at)
            VALUES (?, ?, ?, 'published', ?, ?, ?, ?)
            """,
            (video_id, brand_id, platform, platform_post_id, post_url, now, now),
        )
        logger.info(
            "Published video %d on %s/%s: %s",
            video_id, brand_id, platform, post_url,
        )

    async def _handle_failure(
        self,
        queue_id: int,
        video_id: int,
        brand_id: str,
        platform: str,
        error: str,
    ) -> None:
        """Handle a failed publish attempt.

        Side Effects
        ------------
        * Increments retry count.
        * Reschedules or marks as permanently failed.
        * Sends Telegram alert if retries exhausted.
        """
        # Get current retry count
        row = await self.db.fetch_one(
            """
            SELECT COUNT(*) AS retries FROM publish_log
            WHERE video_id = ? AND platform = ? AND status = 'failed'
            """,
            (video_id, platform),
        )
        retries = (row["retries"] if row else 0) + 1

        if retries < MAX_RETRIES:
            # Reschedule
            next_time = await self.scheduler.calculate_publish_time(
                brand_id, platform
            )
            await self.db.execute(
                "UPDATE content_queue SET scheduled_for = ? WHERE id = ?",
                (next_time.isoformat(), queue_id),
            )
            logger.warning(
                "Retry %d/%d for video %d on %s — rescheduled to %s",
                retries, MAX_RETRIES, video_id, platform, next_time.isoformat(),
            )
        else:
            # Permanently failed
            await self.db.execute(
                "UPDATE content_queue SET status = 'failed' WHERE id = ?",
                (queue_id,),
            )
            logger.error(
                "Video %d permanently failed on %s after %d retries: %s",
                video_id, platform, retries, error,
            )
            # Alert
            if self.notifier:
                try:
                    await self.notifier.send(
                        f"🚨 Publish failed permanently\n"
                        f"Video: {video_id}\nBrand: {brand_id}\n"
                        f"Platform: {platform}\nError: {error}"
                    )
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_publish_data(
        video: Dict[str, Any], brand_id: str, platform: str
    ) -> Dict[str, Any]:
        """Build the publish_data dict from a video record.

        Parameters
        ----------
        video:
            Video DB row as dict.
        brand_id:
            Brand identifier.
        platform:
            Platform name.

        Returns
        -------
        Dict[str, Any]
        """
        import json

        return {
            "video_path": video.get("file_path", ""),
            "thumbnail_path": video.get("thumbnail_path", ""),
            "title": video.get("title", ""),
            "description": video.get("description", ""),
            "captions": video.get("caption", ""),
            "hashtags": json.loads(video["hashtags"]) if video.get("hashtags") else [],
            "brand_id": brand_id,
            "platform": platform,
        }

    # ------------------------------------------------------------------
    # Status queries
    # ------------------------------------------------------------------

    async def get_publication_status(
        self, video_id: int
    ) -> Dict[str, Any]:
        """Return publication status across all platforms.

        Parameters
        ----------
        video_id:
            Video primary key.

        Returns
        -------
        Dict[str, Any]
        """
        rows = await self.db.fetch_all(
            """
            SELECT platform, status, platform_post_id, post_url, published_at
            FROM published_videos
            WHERE video_id = ?
            """,
            (video_id,),
        )
        platforms = {}
        for row in rows:
            platforms[row["platform"]] = {
                "status": row["status"],
                "post_url": row["post_url"],
                "published_at": row["published_at"],
            }
        return {"video_id": video_id, "platforms": platforms}

    async def cancel_scheduled(
        self, video_id: int, platform: Optional[str] = None
    ) -> bool:
        """Cancel a scheduled publish.

        Parameters
        ----------
        video_id:
            Video primary key.
        platform:
            Specific platform; ``None`` = all.

        Returns
        -------
        bool
        """
        if platform:
            await self.db.execute(
                "UPDATE content_queue SET status = 'cancelled' WHERE video_id = ? AND platform = ?",
                (video_id, platform),
            )
        else:
            await self.db.execute(
                "UPDATE content_queue SET status = 'cancelled' WHERE video_id = ?",
                (video_id,),
            )
        logger.info("Cancelled publish for video %d (platform=%s)", video_id, platform or "all")
        return True
