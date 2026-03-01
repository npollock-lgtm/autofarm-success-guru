"""
Review Gate — main orchestrator for the human-review workflow.

Coordinates:
  * Receiving assembled videos from the quality gate
  * Routing reviews to Telegram (primary) or email (fallback)
  * Tracking review requests in the database
  * Managing state transitions: pending_review → approved/rejected → publish queue

Rule #9 (Part 20): ``QualityGate.check()`` runs BEFORE ``ReviewGate.process()``.
Telegram review is primary, email is fallback.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("autofarm.review_gate.gate")


# ---------------------------------------------------------------------------
# ReviewGate
# ---------------------------------------------------------------------------


class ReviewGate:
    """Orchestrate the review workflow for assembled videos.

    Parameters
    ----------
    db:
        Database helper instance.
    approval_tracker:
        ``ApprovalTracker`` for token management.
    telegram_reviewer:
        ``TelegramReviewer`` (primary review channel).
    email_sender:
        ``ReviewEmailSender`` (fallback review channel).
    content_queue:
        ``ContentQueue`` for post-approval queuing.
    auto_approve:
        Whether to enable auto-approval on expiry.
    """

    def __init__(
        self,
        db: Any,
        approval_tracker: Any,
        telegram_reviewer: Any,
        email_sender: Optional[Any] = None,
        content_queue: Optional[Any] = None,
        auto_approve: bool = False,
    ) -> None:
        self.db = db
        self.tracker = approval_tracker
        self.telegram = telegram_reviewer
        self.email = email_sender
        self.content_queue = content_queue
        self.auto_approve = auto_approve

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def process(
        self,
        video_id: int,
        brand_id: str,
        script_text: str,
        video_path: str,
        thumbnail_path: str,
        platforms: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Send a video for human review.

        Parameters
        ----------
        video_id:
            Primary key in the ``videos`` table.
        brand_id:
            Brand identifier.
        script_text:
            The full voiceover script.
        video_path:
            Path to the assembled video.
        thumbnail_path:
            Path to the thumbnail image.
        platforms:
            Target platforms for publishing.
        metadata:
            Optional extra metadata (duration, hook_type, etc.).

        Returns
        -------
        bool
            ``True`` if the review was successfully sent.

        Side Effects
        ------------
        * Creates a review record in the database.
        * Generates an approval token.
        * Sends via Telegram (primary) or email (fallback).
        """
        if platforms is None:
            platforms = ["tiktok", "instagram", "youtube"]
        if metadata is None:
            metadata = {}

        # 1. Create review request
        review_id = await self.create_review_request(
            video_id=video_id,
            brand_id=brand_id,
            script_text=script_text,
            platforms=platforms,
        )

        # 2. Generate approval token
        token = await self.tracker.create_token(
            review_id=review_id,
            brand_id=brand_id,
            auto_approve=self.auto_approve,
        )

        # 3. Try Telegram first (primary)
        try:
            sent = await self.telegram.send_review(
                review_id=review_id,
                brand_id=brand_id,
                video_path=video_path,
                thumbnail_path=thumbnail_path,
                script_text=script_text,
                review_token=token,
                metadata=metadata,
            )
            if sent:
                logger.info(
                    "Review %d sent via Telegram for brand %s", review_id, brand_id
                )
                return True
        except Exception as exc:
            logger.warning("Telegram review failed: %s — falling back to email", exc)

        # 4. Fallback to email
        if self.email:
            try:
                sent = await self.email.send_review_email(
                    review_id=review_id,
                    brand_id=brand_id,
                    video_path=video_path,
                    thumbnail_path=thumbnail_path,
                    script_text=script_text,
                    review_token=token,
                    metadata=metadata,
                )
                if sent:
                    logger.info(
                        "Review %d sent via email for brand %s", review_id, brand_id
                    )
                    return True
            except Exception as exc:
                logger.error("Email review also failed: %s", exc)

        logger.error(
            "Failed to send review %d for brand %s via any channel",
            review_id, brand_id,
        )
        return False

    # ------------------------------------------------------------------
    # Review CRUD
    # ------------------------------------------------------------------

    async def create_review_request(
        self,
        video_id: int,
        brand_id: str,
        script_text: str,
        platforms: List[str],
    ) -> int:
        """Create a new review request in the database.

        Parameters
        ----------
        video_id:
            Video primary key.
        brand_id:
            Brand identifier.
        script_text:
            Script body (stored for reference).
        platforms:
            Target platforms.

        Returns
        -------
        int
            The review_id (auto-incremented PK).

        Side Effects
        ------------
        Inserts a row into the ``reviews`` table.
        """
        import json

        now = datetime.now(timezone.utc).isoformat()
        cursor = await self.db.execute(
            """
            INSERT INTO reviews
                (video_id, brand_id, status, platforms, created_at)
            VALUES (?, ?, 'pending_review', ?, ?)
            """,
            (video_id, brand_id, json.dumps(platforms), now),
        )
        review_id = cursor if isinstance(cursor, int) else 0
        logger.info("Created review request %d for video %d", review_id, video_id)
        return review_id

    async def get_pending_reviews(self) -> List[Dict[str, Any]]:
        """Return all reviews awaiting approval.

        Returns
        -------
        List[Dict[str, Any]]
        """
        rows = await self.db.fetch_all(
            """
            SELECT id, video_id, brand_id, status, platforms, created_at
            FROM reviews
            WHERE status = 'pending_review'
            ORDER BY created_at ASC
            """
        )
        return [dict(r) for r in rows]

    async def check_status(self, review_id: int) -> str:
        """Check the current approval status of a review.

        Parameters
        ----------
        review_id:
            Review primary key.

        Returns
        -------
        str
            Status string: ``pending_review``, ``approved``, ``rejected``, ``expired``.
        """
        row = await self.db.fetch_one(
            "SELECT status FROM reviews WHERE id = ?",
            (review_id,),
        )
        return row["status"] if row else "unknown"

    # ------------------------------------------------------------------
    # Decision handlers
    # ------------------------------------------------------------------

    async def handle_approval(self, review_id: int) -> bool:
        """Process an approval decision.

        Parameters
        ----------
        review_id:
            Review primary key.

        Returns
        -------
        bool
            ``True`` on success.

        Side Effects
        ------------
        * Updates review status to ``'approved'``.
        * Adds video to the content queue.
        """
        row = await self.db.fetch_one(
            "SELECT video_id, brand_id, platforms FROM reviews WHERE id = ?",
            (review_id,),
        )
        if not row:
            return False

        import json

        await self.db.execute(
            "UPDATE reviews SET status = 'approved' WHERE id = ?",
            (review_id,),
        )

        # Add to content queue
        if self.content_queue:
            platforms = json.loads(row["platforms"]) if row["platforms"] else []
            await self.content_queue.add_to_queue(
                video_id=row["video_id"],
                brand_id=row["brand_id"],
                platforms=platforms,
            )

        logger.info("Review %d approved — added to publish queue", review_id)
        return True

    async def handle_rejection(
        self, review_id: int, reason: str = ""
    ) -> bool:
        """Process a rejection decision.

        Parameters
        ----------
        review_id:
            Review primary key.
        reason:
            Optional rejection reason.

        Returns
        -------
        bool
            ``True`` on success.

        Side Effects
        ------------
        Updates review status to ``'rejected'``.
        """
        await self.db.execute(
            "UPDATE reviews SET status = 'rejected' WHERE id = ?",
            (review_id,),
        )
        logger.info("Review %d rejected: %s", review_id, reason)
        return True

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def flush_stale_reviews(self, max_age_days: int = 14) -> int:
        """Remove reviews older than *max_age_days* that are still pending.

        Parameters
        ----------
        max_age_days:
            Maximum age before auto-expiry.

        Returns
        -------
        int
            Number of expired reviews.
        """
        from datetime import timedelta

        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=max_age_days)
        ).isoformat()
        result = await self.db.execute(
            """
            UPDATE reviews SET status = 'expired'
            WHERE status = 'pending_review' AND created_at < ?
            """,
            (cutoff,),
        )
        count = result if isinstance(result, int) else 0
        if count:
            logger.info("Expired %d stale reviews", count)
        return count
