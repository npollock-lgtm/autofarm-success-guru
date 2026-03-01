"""
Analytics Puller — fetches real engagement metrics from each platform's API
and stores them in the ``analytics`` table.

Runs daily at 03:00 UTC via ``jobs/pull_analytics.py``.  Each platform has
its own pull method that maps the vendor-specific JSON to our canonical
schema (views, likes, comments, shares, saves, watch_time, etc.).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("autofarm.feedback_loop.analytics_puller")

# How far back to pull for videos without analytics yet (days)
DEFAULT_LOOKBACK_DAYS = 30
# Minimum hours after publish before first pull
MIN_HOURS_AFTER_PUBLISH = 24


class AnalyticsPuller:
    """Pull analytics from platform APIs for all published videos.

    Parameters
    ----------
    db:
        Database helper instance.
    rate_limiter:
        ``RateLimitManager`` for API call compliance.
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
    # Primary API
    # ------------------------------------------------------------------

    async def pull_all_analytics(self) -> Dict[str, Any]:
        """Main entry: pull analytics for all brands × platforms.

        Returns
        -------
        Dict[str, Any]
            ``{successful_pulls, failed_pulls, errors}``

        Side Effects
        ------------
        Inserts/updates rows in the ``analytics`` table.
        """
        jobs = await self._get_pullable_jobs()
        successful = 0
        failed = 0
        errors: List[str] = []

        for job in jobs:
            try:
                metrics = await self.pull_analytics_for_platform(
                    job["brand_id"], job["platform"], job["platform_post_id"],
                    job["id"],
                )
                if metrics:
                    await self.store_analytics(job["id"], job["brand_id"],
                                               job["platform"],
                                               job["platform_post_id"],
                                               metrics)
                    successful += 1
                else:
                    failed += 1
                    errors.append(
                        f"No metrics for job {job['id']} on {job['platform']}"
                    )
            except Exception as exc:
                failed += 1
                errors.append(f"Job {job['id']}: {exc}")
                logger.error("Analytics pull failed for job %d: %s",
                             job["id"], exc)

        logger.info(
            "Analytics pull complete: %d success, %d failed",
            successful, failed,
        )
        return {
            "successful_pulls": successful,
            "failed_pulls": failed,
            "errors": errors,
        }

    # ------------------------------------------------------------------
    # Platform dispatcher
    # ------------------------------------------------------------------

    async def pull_analytics_for_platform(
        self,
        brand_id: str,
        platform: str,
        platform_post_id: str,
        publish_job_id: int,
    ) -> Optional[Dict[str, Any]]:
        """Dispatch to the platform-specific puller.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        platform:
            Platform name.
        platform_post_id:
            Platform-side post/video ID.
        publish_job_id:
            Local publish_jobs row id.

        Returns
        -------
        Optional[Dict[str, Any]]
            Canonical metrics dict or ``None`` on failure.
        """
        handlers = {
            "tiktok": self._pull_tiktok,
            "instagram": self._pull_instagram,
            "facebook": self._pull_facebook,
            "youtube": self._pull_youtube,
            "snapchat": self._pull_snapchat,
        }
        handler = handlers.get(platform)
        if not handler:
            logger.error("No analytics handler for platform: %s", platform)
            return None

        return await handler(brand_id, platform_post_id)

    # ------------------------------------------------------------------
    # TikTok
    # ------------------------------------------------------------------

    async def _pull_tiktok(
        self, brand_id: str, post_id: str
    ) -> Optional[Dict[str, Any]]:
        """Fetch TikTok video analytics via the official API.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        post_id:
            TikTok video ID.

        Returns
        -------
        Optional[Dict[str, Any]]
            Canonical metrics dict.
        """
        import aiohttp

        creds = await self.credential_manager.get_credentials(brand_id, "tiktok")
        if not creds or not creds.get("access_token"):
            logger.warning("No TikTok credentials for %s", brand_id)
            return None

        allowed = await self.rate_limiter.check_and_increment(
            brand_id, "tiktok", "analytics", units=1
        )
        if not allowed:
            logger.warning("Rate limited: TikTok analytics for %s", brand_id)
            return None

        session = await self.ip_router.get_session(brand_id, "tiktok")
        headers = {"Authorization": f"Bearer {creds['access_token']}"}
        url = (
            "https://open.tiktokapis.com/v2/video/query/"
            f"?fields=id,like_count,comment_count,share_count,"
            f"view_count,play_count"
        )
        body = {"filters": {"video_ids": [post_id]}}

        try:
            async with session.post(url, json=body, headers=headers,
                                    timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    logger.warning("TikTok API %d for %s", resp.status, post_id)
                    return None
                data = await resp.json()

            videos = data.get("data", {}).get("videos", [])
            if not videos:
                return None

            v = videos[0]
            return self._canonical_metrics(
                views=v.get("view_count", 0),
                likes=v.get("like_count", 0),
                comments=v.get("comment_count", 0),
                shares=v.get("share_count", 0),
            )
        except Exception as exc:
            logger.error("TikTok analytics error: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Instagram
    # ------------------------------------------------------------------

    async def _pull_instagram(
        self, brand_id: str, post_id: str
    ) -> Optional[Dict[str, Any]]:
        """Fetch Instagram Reels insights via Meta Graph API.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        post_id:
            Instagram media ID.

        Returns
        -------
        Optional[Dict[str, Any]]
            Canonical metrics dict.
        """
        import aiohttp

        creds = await self.credential_manager.get_credentials(brand_id, "instagram")
        if not creds or not creds.get("access_token"):
            return None

        allowed = await self.rate_limiter.check_and_increment(
            brand_id, "instagram", "analytics", units=1
        )
        if not allowed:
            return None

        session = await self.ip_router.get_session(brand_id, "instagram")
        token = creds["access_token"]

        # Basic media fields
        url = (
            f"https://graph.facebook.com/v18.0/{post_id}"
            f"?fields=like_count,comments_count,timestamp"
            f"&access_token={token}"
        )
        # Insights
        insights_url = (
            f"https://graph.facebook.com/v18.0/{post_id}/insights"
            f"?metric=impressions,reach,saved,shares,video_views"
            f"&access_token={token}"
        )

        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                basic = await resp.json() if resp.status == 200 else {}

            async with session.get(insights_url,
                                   timeout=aiohttp.ClientTimeout(total=30)) as resp:
                insights_data = await resp.json() if resp.status == 200 else {}

            # Parse insights
            insights_map: Dict[str, int] = {}
            for item in insights_data.get("data", []):
                name = item.get("name", "")
                values = item.get("values", [{}])
                insights_map[name] = values[0].get("value", 0) if values else 0

            return self._canonical_metrics(
                views=insights_map.get("video_views", 0),
                likes=basic.get("like_count", 0),
                comments=basic.get("comments_count", 0),
                shares=insights_map.get("shares", 0),
                saves=insights_map.get("saved", 0),
                impressions=insights_map.get("impressions", 0),
                reach=insights_map.get("reach", 0),
            )
        except Exception as exc:
            logger.error("Instagram analytics error: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Facebook
    # ------------------------------------------------------------------

    async def _pull_facebook(
        self, brand_id: str, post_id: str
    ) -> Optional[Dict[str, Any]]:
        """Fetch Facebook video analytics via Graph API.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        post_id:
            Facebook post ID.

        Returns
        -------
        Optional[Dict[str, Any]]
            Canonical metrics dict.
        """
        import aiohttp

        creds = await self.credential_manager.get_credentials(brand_id, "facebook")
        if not creds or not creds.get("access_token"):
            return None

        allowed = await self.rate_limiter.check_and_increment(
            brand_id, "facebook", "analytics", units=1
        )
        if not allowed:
            return None

        session = await self.ip_router.get_session(brand_id, "facebook")
        token = creds["access_token"]

        url = (
            f"https://graph.facebook.com/v18.0/{post_id}"
            f"?fields=shares,reactions.summary(true),"
            f"comments.summary(true)"
            f"&access_token={token}"
        )
        insights_url = (
            f"https://graph.facebook.com/v18.0/{post_id}/insights"
            f"?metric=post_impressions,post_engaged_users,"
            f"post_video_views,post_video_avg_time_watched"
            f"&access_token={token}"
        )

        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                basic = await resp.json() if resp.status == 200 else {}

            async with session.get(insights_url,
                                   timeout=aiohttp.ClientTimeout(total=30)) as resp:
                insights_data = await resp.json() if resp.status == 200 else {}

            insights_map: Dict[str, int] = {}
            for item in insights_data.get("data", []):
                name = item.get("name", "")
                values = item.get("values", [{}])
                insights_map[name] = values[0].get("value", 0) if values else 0

            shares_data = basic.get("shares", {})
            return self._canonical_metrics(
                views=insights_map.get("post_video_views", 0),
                likes=basic.get("reactions", {}).get("summary", {}).get(
                    "total_count", 0
                ),
                comments=basic.get("comments", {}).get("summary", {}).get(
                    "total_count", 0
                ),
                shares=shares_data.get("count", 0) if isinstance(shares_data, dict) else 0,
                impressions=insights_map.get("post_impressions", 0),
                watch_time_seconds=insights_map.get(
                    "post_video_avg_time_watched", 0
                ),
            )
        except Exception as exc:
            logger.error("Facebook analytics error: %s", exc)
            return None

    # ------------------------------------------------------------------
    # YouTube
    # ------------------------------------------------------------------

    async def _pull_youtube(
        self, brand_id: str, video_id: str
    ) -> Optional[Dict[str, Any]]:
        """Fetch YouTube video statistics via Data API v3.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        video_id:
            YouTube video ID.

        Returns
        -------
        Optional[Dict[str, Any]]
            Canonical metrics dict.
        """
        import aiohttp

        creds = await self.credential_manager.get_credentials(brand_id, "youtube")
        if not creds or not creds.get("access_token"):
            return None

        allowed = await self.rate_limiter.check_and_increment(
            brand_id, "youtube", "analytics", units=1
        )
        if not allowed:
            return None

        session = await self.ip_router.get_session(brand_id, "youtube")
        token = creds["access_token"]

        url = (
            f"https://www.googleapis.com/youtube/v3/videos"
            f"?part=statistics,contentDetails"
            f"&id={video_id}"
            f"&access_token={token}"
        )

        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()

            items = data.get("items", [])
            if not items:
                return None

            stats = items[0].get("statistics", {})
            return self._canonical_metrics(
                views=int(stats.get("viewCount", 0)),
                likes=int(stats.get("likeCount", 0)),
                comments=int(stats.get("commentCount", 0)),
                shares=0,  # Not available via basic API
                saves=int(stats.get("favoriteCount", 0)),
            )
        except Exception as exc:
            logger.error("YouTube analytics error: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Snapchat
    # ------------------------------------------------------------------

    async def _pull_snapchat(
        self, brand_id: str, snap_id: str
    ) -> Optional[Dict[str, Any]]:
        """Fetch Snapchat Spotlight analytics.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        snap_id:
            Snapchat media ID.

        Returns
        -------
        Optional[Dict[str, Any]]
            Canonical metrics dict.
        """
        import aiohttp

        creds = await self.credential_manager.get_credentials(brand_id, "snapchat")
        if not creds or not creds.get("access_token"):
            return None

        allowed = await self.rate_limiter.check_and_increment(
            brand_id, "snapchat", "analytics", units=1
        )
        if not allowed:
            return None

        session = await self.ip_router.get_session(brand_id, "snapchat")
        token = creds["access_token"]

        url = (
            f"https://adsapi.snapchat.com/v1/media/{snap_id}/stats"
        )
        headers = {"Authorization": f"Bearer {token}"}

        try:
            async with session.get(url, headers=headers,
                                   timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()

            stats = data.get("stats", {})
            return self._canonical_metrics(
                views=stats.get("impressions", 0),
                likes=0,
                comments=0,
                shares=stats.get("swipe_ups", 0),
                watch_time_seconds=stats.get("screen_time_millis", 0) / 1000,
            )
        except Exception as exc:
            logger.error("Snapchat analytics error: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------

    async def store_analytics(
        self,
        publish_job_id: int,
        brand_id: str,
        platform: str,
        platform_post_id: str,
        metrics: Dict[str, Any],
    ) -> None:
        """Insert a new analytics snapshot.

        Parameters
        ----------
        publish_job_id:
            Local publish_jobs row id.
        brand_id:
            Brand identifier.
        platform:
            Platform name.
        platform_post_id:
            Platform-side post/video ID.
        metrics:
            Canonical metrics dict from puller.

        Side Effects
        ------------
        Inserts row into ``analytics`` table.
        """
        now = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            """
            INSERT INTO analytics
                (publish_job_id, brand_id, platform, platform_post_id,
                 views, likes, comments, shares, saves,
                 watch_time_seconds, avg_view_duration_seconds,
                 retention_rate, three_second_hold_rate,
                 impressions, reach, engagement_rate, pulled_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                publish_job_id, brand_id, platform, platform_post_id,
                metrics.get("views", 0),
                metrics.get("likes", 0),
                metrics.get("comments", 0),
                metrics.get("shares", 0),
                metrics.get("saves", 0),
                metrics.get("watch_time_seconds", 0),
                metrics.get("avg_view_duration_seconds", 0),
                metrics.get("retention_rate", 0),
                metrics.get("three_second_hold_rate", 0),
                metrics.get("impressions", 0),
                metrics.get("reach", 0),
                metrics.get("engagement_rate", 0),
                now,
            ),
        )
        logger.info(
            "Stored analytics for job %d (%s/%s): %d views",
            publish_job_id, brand_id, platform, metrics.get("views", 0),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get_pullable_jobs(self) -> List[Dict[str, Any]]:
        """Find published jobs eligible for analytics pull.

        Returns
        -------
        List[Dict[str, Any]]
            Jobs that were published ≥ 24 h ago and within the lookback window.
        """
        min_age = (
            datetime.now(timezone.utc) - timedelta(hours=MIN_HOURS_AFTER_PUBLISH)
        ).isoformat()
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=DEFAULT_LOOKBACK_DAYS)
        ).isoformat()

        rows = await self.db.fetch_all(
            """
            SELECT id, brand_id, platform, platform_post_id, published_at
            FROM publish_jobs
            WHERE status = 'published'
                  AND platform_post_id IS NOT NULL
                  AND published_at IS NOT NULL
                  AND published_at <= ?
                  AND published_at >= ?
            ORDER BY published_at DESC
            """,
            (min_age, cutoff),
        )
        return [dict(r) for r in rows]

    @staticmethod
    def _canonical_metrics(
        views: int = 0,
        likes: int = 0,
        comments: int = 0,
        shares: int = 0,
        saves: int = 0,
        watch_time_seconds: float = 0,
        avg_view_duration_seconds: float = 0,
        retention_rate: float = 0,
        three_second_hold_rate: float = 0,
        impressions: int = 0,
        reach: int = 0,
    ) -> Dict[str, Any]:
        """Build a canonical metrics dict.

        Parameters
        ----------
        views, likes, comments, shares, saves:
            Engagement counts.
        watch_time_seconds:
            Total watch time.
        avg_view_duration_seconds:
            Average view duration.
        retention_rate:
            Fraction of video watched on average.
        three_second_hold_rate:
            Fraction of viewers who watched ≥ 3 seconds.
        impressions:
            Number of times the video was displayed.
        reach:
            Unique accounts reached.

        Returns
        -------
        Dict[str, Any]
        """
        engagement_rate = 0.0
        if impressions > 0:
            engagement_rate = (likes + comments + shares + saves) / impressions

        return {
            "views": views,
            "likes": likes,
            "comments": comments,
            "shares": shares,
            "saves": saves,
            "watch_time_seconds": watch_time_seconds,
            "avg_view_duration_seconds": avg_view_duration_seconds,
            "retention_rate": retention_rate,
            "three_second_hold_rate": three_second_hold_rate,
            "impressions": impressions,
            "reach": reach,
            "engagement_rate": round(engagement_rate, 6),
        }
