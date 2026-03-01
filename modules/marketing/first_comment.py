"""
First Comment Poster — automatically posts a pinned first comment after
each video is published.  Uses brand-specific templates to drive
engagement and set the conversation tone.

The comment is posted via each platform's API through ``BrandIPRouter``
and ``RateLimitManager`` as required by Part 20.
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("autofarm.marketing.first_comment")

# Brand-specific comment templates
# Each brand has engagement-driving templates appropriate to its voice
COMMENT_TEMPLATES: Dict[str, List[str]] = {
    "human_success_guru": [
        "Drop a \U0001f525 if this hit different today",
        "Which of these do you struggle with most? \U0001f447",
        "Tag someone who needs to hear this \U0001f4af",
        "Save this for when you need a reminder \U0001f4cc",
        "Comment 'READY' if you're making this change today",
        "What's the one thing holding you back? Tell me below \U0001f447",
    ],
    "wealth_success_guru": [
        "What's your current income goal? Drop it below \U0001f4b0",
        "Save this — you'll want to come back to it \U0001f4cc",
        "Which tip are you implementing first? \U0001f447",
        "Tag someone who's on their wealth journey \U0001f4b8",
        "Comment 'WEALTH' if you're serious about this",
        "Most people won't do step 3 — will you? \U0001f447",
    ],
    "zen_success_guru": [
        "Take a deep breath before you scroll \U0001f30a",
        "Which habit brings you the most peace? Share below \U0001f331",
        "Save this for your morning routine \U0001f4cc",
        "Tag someone who could use some calm today \U0001f49a",
        "Comment '\U0001f9d8' if you're practising this today",
        "What does peace look like for you? Tell me below \U0001f447",
    ],
    "social_success_guru": [
        "What's your biggest social challenge? Drop it below \U0001f447",
        "Tag your most confident friend \U0001f4aa",
        "Save this for your next conversation \U0001f4cc",
        "Which tip changed the game for you? \U0001f447",
        "Comment 'SOCIAL' if you're levelling up your skills",
        "Try tip #1 today and tell me how it goes \U0001f447",
    ],
    "habits_success_guru": [
        "What's the ONE habit you're building right now? \U0001f447",
        "Save this and check back in 30 days \U0001f4cc",
        "Tag your accountability partner \U0001f91d",
        "Comment 'DAY 1' if you're starting today",
        "Which of these habits would change your life most? \U0001f447",
        "Small steps = big results. What's your first step? \U0001f447",
    ],
    "relationships_success_guru": [
        "Tag someone who makes your life better \U00002764\U0000fe0f",
        "What's the best relationship advice you've received? \U0001f447",
        "Save this for when you need it most \U0001f4cc",
        "Comment 'LOVE' if this resonates with you",
        "Which tip do you wish you'd learned sooner? \U0001f447",
        "Try this tonight and let me know how it goes \U0001f447",
    ],
}

# Default templates for unknown brands
DEFAULT_TEMPLATES: List[str] = [
    "What do you think? Drop your thoughts below \U0001f447",
    "Save this for later \U0001f4cc",
    "Tag someone who needs to see this",
    "Comment if this resonated with you",
]


class FirstCommentPoster:
    """Post an engagement-driving first comment after publishing.

    Parameters
    ----------
    db:
        Database helper instance.
    rate_limiter:
        ``RateLimitManager`` for API compliance.
    credential_manager:
        ``CredentialManager`` for token access.
    ip_router:
        ``BrandIPRouter`` for session management.
    """

    def __init__(
        self,
        db: Any,
        rate_limiter: Any,
        credential_manager: Any,
        ip_router: Any,
    ) -> None:
        self.db = db
        self.rate_limiter = rate_limiter
        self.credential_manager = credential_manager
        self.ip_router = ip_router

    # ------------------------------------------------------------------
    # Post first comment
    # ------------------------------------------------------------------

    async def post_first_comment(
        self,
        publish_job_id: int,
        brand_id: str,
        platform: str,
        platform_post_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Select and post a brand-appropriate first comment.

        Parameters
        ----------
        publish_job_id:
            ``publish_jobs.id``.
        brand_id:
            Brand identifier.
        platform:
            Platform name.
        platform_post_id:
            Platform-side post/video ID.

        Returns
        -------
        Optional[Dict[str, Any]]
            ``{comment_id, comment_text, platform}`` or ``None`` on failure.

        Side Effects
        ------------
        Posts comment via platform API.
        Inserts row into ``first_comments`` table.
        """
        # Check we haven't already commented
        existing = await self.db.fetch_one(
            """
            SELECT id FROM first_comments
            WHERE publish_job_id = ? AND platform = ?
                  AND status IN ('posted', 'pending')
            """,
            (publish_job_id, platform),
        )
        if existing:
            logger.info(
                "First comment already exists for job %d/%s",
                publish_job_id, platform,
            )
            return None

        # Select comment text
        comment_text = self._select_comment(brand_id, platform)

        # Store pending record
        now = datetime.now(timezone.utc).isoformat()
        comment_row_id = await self.db.execute(
            """
            INSERT INTO first_comments
                (publish_job_id, brand_id, platform, comment_text, status)
            VALUES (?, ?, ?, ?, 'pending')
            """,
            (publish_job_id, brand_id, platform, comment_text),
        )

        # Post via platform API
        comment_id = await self._post_comment(
            brand_id, platform, platform_post_id, comment_text
        )

        if comment_id:
            await self.db.execute(
                """
                UPDATE first_comments
                SET comment_id = ?, posted_at = ?, status = 'posted'
                WHERE id = ?
                """,
                (comment_id, now, comment_row_id),
            )
            logger.info(
                "Posted first comment on %s/%s for job %d",
                brand_id, platform, publish_job_id,
            )
            return {
                "comment_id": comment_id,
                "comment_text": comment_text,
                "platform": platform,
            }
        else:
            await self.db.execute(
                "UPDATE first_comments SET status = 'failed' WHERE id = ?",
                (comment_row_id,),
            )
            logger.warning(
                "Failed to post first comment for job %d on %s",
                publish_job_id, platform,
            )
            return None

    # ------------------------------------------------------------------
    # Platform dispatchers
    # ------------------------------------------------------------------

    async def _post_comment(
        self,
        brand_id: str,
        platform: str,
        post_id: str,
        text: str,
    ) -> Optional[str]:
        """Dispatch to the platform-specific comment API.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        platform:
            Platform name.
        post_id:
            Platform-side post ID.
        text:
            Comment text.

        Returns
        -------
        Optional[str]
            Platform-side comment ID or ``None``.
        """
        handlers = {
            "tiktok": self._post_tiktok_comment,
            "instagram": self._post_instagram_comment,
            "facebook": self._post_facebook_comment,
            "youtube": self._post_youtube_comment,
        }
        handler = handlers.get(platform)
        if not handler:
            logger.info("First comments not supported on %s", platform)
            return None

        return await handler(brand_id, post_id, text)

    async def _post_tiktok_comment(
        self, brand_id: str, video_id: str, text: str
    ) -> Optional[str]:
        """Post a comment on TikTok via Content Posting API.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        video_id:
            TikTok video ID.
        text:
            Comment text.

        Returns
        -------
        Optional[str]
            Comment ID or ``None``.
        """
        import aiohttp

        allowed = await self.rate_limiter.check_and_increment(
            brand_id, "tiktok", "comment", units=1
        )
        if not allowed:
            logger.warning("Rate limited: TikTok comment for %s", brand_id)
            return None

        creds = await self.credential_manager.get_credentials(brand_id, "tiktok")
        if not creds or not creds.get("access_token"):
            return None

        session = await self.ip_router.get_session(brand_id, "tiktok")
        headers = {"Authorization": f"Bearer {creds['access_token']}"}
        url = "https://open.tiktokapis.com/v2/comment/publish/"
        body = {"video_id": video_id, "text": text}

        try:
            async with session.post(url, json=body, headers=headers,
                                    timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("data", {}).get("comment_id")
                logger.warning("TikTok comment API returned %d", resp.status)
                return None
        except Exception as exc:
            logger.error("TikTok comment error: %s", exc)
            return None

    async def _post_instagram_comment(
        self, brand_id: str, media_id: str, text: str
    ) -> Optional[str]:
        """Post a comment on Instagram via Graph API.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        media_id:
            Instagram media ID.
        text:
            Comment text.

        Returns
        -------
        Optional[str]
            Comment ID or ``None``.
        """
        import aiohttp

        allowed = await self.rate_limiter.check_and_increment(
            brand_id, "instagram", "comment", units=1
        )
        if not allowed:
            return None

        creds = await self.credential_manager.get_credentials(brand_id, "instagram")
        if not creds or not creds.get("access_token"):
            return None

        session = await self.ip_router.get_session(brand_id, "instagram")
        token = creds["access_token"]
        url = (
            f"https://graph.facebook.com/v18.0/{media_id}/comments"
            f"?message={text}&access_token={token}"
        )

        try:
            async with session.post(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("id")
                return None
        except Exception as exc:
            logger.error("Instagram comment error: %s", exc)
            return None

    async def _post_facebook_comment(
        self, brand_id: str, post_id: str, text: str
    ) -> Optional[str]:
        """Post a comment on Facebook via Graph API.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        post_id:
            Facebook post ID.
        text:
            Comment text.

        Returns
        -------
        Optional[str]
            Comment ID or ``None``.
        """
        import aiohttp

        allowed = await self.rate_limiter.check_and_increment(
            brand_id, "facebook", "comment", units=1
        )
        if not allowed:
            return None

        creds = await self.credential_manager.get_credentials(brand_id, "facebook")
        if not creds or not creds.get("access_token"):
            return None

        session = await self.ip_router.get_session(brand_id, "facebook")
        token = creds["access_token"]
        url = f"https://graph.facebook.com/v18.0/{post_id}/comments"

        try:
            async with session.post(
                url,
                data={"message": text, "access_token": token},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("id")
                return None
        except Exception as exc:
            logger.error("Facebook comment error: %s", exc)
            return None

    async def _post_youtube_comment(
        self, brand_id: str, video_id: str, text: str
    ) -> Optional[str]:
        """Post a comment on YouTube via Data API v3.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        video_id:
            YouTube video ID.
        text:
            Comment text.

        Returns
        -------
        Optional[str]
            Comment ID or ``None``.
        """
        import aiohttp

        # YouTube comment insert costs 50 quota units
        allowed = await self.rate_limiter.check_and_increment(
            brand_id, "youtube", "comment", units=50
        )
        if not allowed:
            return None

        creds = await self.credential_manager.get_credentials(brand_id, "youtube")
        if not creds or not creds.get("access_token"):
            return None

        session = await self.ip_router.get_session(brand_id, "youtube")
        token = creds["access_token"]
        url = (
            "https://www.googleapis.com/youtube/v3/commentThreads"
            "?part=snippet"
        )
        body = {
            "snippet": {
                "videoId": video_id,
                "topLevelComment": {
                    "snippet": {"textOriginal": text}
                },
            }
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        try:
            async with session.post(url, json=body, headers=headers,
                                    timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("id")
                return None
        except Exception as exc:
            logger.error("YouTube comment error: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Comment selection
    # ------------------------------------------------------------------

    def _select_comment(self, brand_id: str, platform: str) -> str:
        """Choose a comment template for the brand.

        Avoids repeating the same comment consecutively by rotating
        through the template list.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        platform:
            Platform name (for future platform-specific templates).

        Returns
        -------
        str
            Selected comment text.
        """
        templates = COMMENT_TEMPLATES.get(brand_id, DEFAULT_TEMPLATES)
        return random.choice(templates)

    # ------------------------------------------------------------------
    # Batch posting
    # ------------------------------------------------------------------

    async def post_pending_comments(self) -> Dict[str, int]:
        """Retry any pending first comments that weren't posted.

        Returns
        -------
        Dict[str, int]
            ``{posted, failed}``

        Side Effects
        ------------
        Posts comments and updates ``first_comments`` status.
        """
        pending = await self.db.fetch_all(
            """
            SELECT fc.id, fc.publish_job_id, fc.brand_id, fc.platform,
                   fc.comment_text, pj.platform_post_id
            FROM first_comments fc
            JOIN publish_jobs pj ON pj.id = fc.publish_job_id
            WHERE fc.status = 'pending' AND pj.platform_post_id IS NOT NULL
            ORDER BY fc.id ASC
            LIMIT 20
            """
        )

        posted = 0
        failed = 0

        for row in pending:
            comment_id = await self._post_comment(
                row["brand_id"], row["platform"],
                row["platform_post_id"], row["comment_text"],
            )
            now = datetime.now(timezone.utc).isoformat()

            if comment_id:
                await self.db.execute(
                    """
                    UPDATE first_comments
                    SET comment_id = ?, posted_at = ?, status = 'posted'
                    WHERE id = ?
                    """,
                    (comment_id, now, row["id"]),
                )
                posted += 1
            else:
                await self.db.execute(
                    "UPDATE first_comments SET status = 'failed' WHERE id = ?",
                    (row["id"],),
                )
                failed += 1

        if posted or failed:
            logger.info("Pending comments: %d posted, %d failed", posted, failed)
        return {"posted": posted, "failed": failed}
