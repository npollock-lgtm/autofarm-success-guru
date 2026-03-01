"""
Smart Scheduler — calculates optimal, varied publish times.

Learns from analytics to shift posting windows toward best-performing hours.
Enforces platform rate limits and minimum brand spacing.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from modules.publish_engine.schedule_config import POSTING_WINDOWS_UTC

logger = logging.getLogger("autofarm.publish_engine.scheduler")

RANDOM_WINDOW_MINUTES = 60  # ±30 min within window
MIN_BRAND_SPACING_MINUTES = 5


class SmartScheduler:
    """Calculate optimal publish times using schedule config and analytics.

    Parameters
    ----------
    db:
        Database helper instance.
    rate_limiter:
        ``RateLimitManager`` instance.
    """

    def __init__(self, db: Any, rate_limiter: Optional[Any] = None) -> None:
        self.db = db
        self.rate_limiter = rate_limiter

    # ------------------------------------------------------------------
    # Primary API
    # ------------------------------------------------------------------

    async def calculate_publish_time(
        self,
        brand_id: str,
        platform: str,
        content_ready_at: Optional[datetime] = None,
    ) -> datetime:
        """Calculate the next optimal publish time for brand × platform.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        platform:
            Platform name.
        content_ready_at:
            Earliest the content is available.

        Returns
        -------
        datetime
            UTC datetime for publishing.

        Raises
        ------
        ValueError
            If no viable window found.
        """
        if content_ready_at is None:
            content_ready_at = datetime.now(timezone.utc)

        brand_schedule = POSTING_WINDOWS_UTC.get(brand_id, {})
        plat_config = brand_schedule.get(platform, {})
        windows = plat_config.get("windows", [[12, 0]])
        best_days = plat_config.get("best_days", [1, 2, 3, 4, 5, 6, 7])
        daily_limit = plat_config.get("daily_limit", 1)

        # Try today and next 7 days
        for day_offset in range(8):
            candidate_date = (content_ready_at + timedelta(days=day_offset)).date()
            iso_day = candidate_date.isoweekday()
            if iso_day not in best_days:
                continue

            # Check daily limit
            used = await self._count_scheduled(brand_id, platform, candidate_date)
            if used >= daily_limit:
                continue

            for idx, (hour, minute) in enumerate(windows):
                candidate = datetime(
                    candidate_date.year, candidate_date.month, candidate_date.day,
                    hour, minute, tzinfo=timezone.utc,
                )

                # Deterministic offset
                offset_mins = self._deterministic_offset(
                    brand_id, platform, candidate_date, idx
                )
                candidate += timedelta(minutes=offset_mins)

                # Must be in the future
                if candidate <= content_ready_at:
                    continue

                # Brand spacing check
                spacing_ok = await self._check_brand_spacing(
                    brand_id, platform, candidate
                )
                if not spacing_ok:
                    candidate += timedelta(minutes=MIN_BRAND_SPACING_MINUTES)

                return candidate

        # Fallback: 1 hour from now
        logger.warning(
            "No optimal window found for %s/%s — using fallback",
            brand_id, platform,
        )
        return content_ready_at + timedelta(hours=1)

    async def schedule_batch(
        self, items: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Distribute multiple videos across windows.

        Parameters
        ----------
        items:
            List of ``{video_id, brand_id, platform}`` dicts.

        Returns
        -------
        List[Dict[str, Any]]
            Each with added ``scheduled_time``.
        """
        results: List[Dict[str, Any]] = []
        last_times: Dict[str, datetime] = {}

        for item in items:
            brand_id = item["brand_id"]
            platform = item["platform"]
            key = f"{brand_id}_{platform}"
            ready = last_times.get(key, datetime.now(timezone.utc))

            scheduled = await self.calculate_publish_time(
                brand_id, platform, ready
            )
            last_times[key] = scheduled + timedelta(minutes=30)

            results.append({**item, "scheduled_time": scheduled.isoformat()})
        return results

    async def get_next_24h_schedule(self) -> Dict[str, Dict[str, List[str]]]:
        """Return all videos scheduled for the next 24 hours.

        Returns
        -------
        Dict[str, Dict[str, List[str]]]
            ``{brand_id: {platform: [iso_times]}}``.
        """
        now = datetime.now(timezone.utc).isoformat()
        end = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()

        rows = await self.db.fetch_all(
            """
            SELECT brand_id, platform, scheduled_for
            FROM content_queue
            WHERE status = 'waiting' AND scheduled_for >= ? AND scheduled_for <= ?
            ORDER BY scheduled_for
            """,
            (now, end),
        )

        schedule: Dict[str, Dict[str, List[str]]] = {}
        for row in rows:
            bid = row["brand_id"]
            plat = row["platform"]
            if bid not in schedule:
                schedule[bid] = {}
            if plat not in schedule[bid]:
                schedule[bid][plat] = []
            schedule[bid][plat].append(row["scheduled_for"])
        return schedule

    async def reoptimise_windows(self) -> None:
        """Weekly job: analyse last 30 days and shift windows toward best hours.

        Side Effects
        ------------
        Logs recommended window shifts (actual config update is manual).
        """
        for brand_id in POSTING_WINDOWS_UTC:
            for platform in POSTING_WINDOWS_UTC[brand_id]:
                ranked = await self.get_performance_ranked_windows(
                    brand_id, platform
                )
                if ranked:
                    best = ranked[0]
                    logger.info(
                        "Best window for %s/%s: %02d:%02d (avg_cps=%.3f, n=%d)",
                        brand_id, platform, best["hour"], best["minute"],
                        best["avg_cps"], best["sample_count"],
                    )

    async def get_performance_ranked_windows(
        self, brand_id: str, platform: str
    ) -> List[Dict[str, Any]]:
        """Query analytics over last 30 days, rank hours by avg CPS.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        platform:
            Platform name.

        Returns
        -------
        List[Dict[str, Any]]
            Sorted by ``avg_cps`` descending.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        rows = await self.db.fetch_all(
            """
            SELECT
                CAST(strftime('%H', published_at) AS INTEGER) AS hour,
                AVG(
                    COALESCE(likes, 0) + COALESCE(comments, 0) + COALESCE(shares, 0)
                ) AS avg_cps,
                COUNT(*) AS sample_count
            FROM published_videos
            WHERE brand_id = ? AND platform = ? AND published_at >= ?
            GROUP BY hour
            HAVING sample_count >= 3
            ORDER BY avg_cps DESC
            """,
            (brand_id, platform, cutoff),
        )
        return [
            {
                "hour": row["hour"],
                "minute": 0,
                "avg_cps": round(row["avg_cps"], 3),
                "sample_count": row["sample_count"],
            }
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _count_scheduled(
        self, brand_id: str, platform: str, target_date: date
    ) -> int:
        """Count videos already scheduled for brand × platform × date."""
        start = datetime(
            target_date.year, target_date.month, target_date.day,
            tzinfo=timezone.utc,
        ).isoformat()
        end = (
            datetime(
                target_date.year, target_date.month, target_date.day,
                tzinfo=timezone.utc,
            )
            + timedelta(days=1)
        ).isoformat()

        row = await self.db.fetch_one(
            """
            SELECT COUNT(*) AS cnt FROM content_queue
            WHERE brand_id = ? AND platform = ?
                  AND scheduled_for >= ? AND scheduled_for < ?
                  AND status IN ('waiting', 'published')
            """,
            (brand_id, platform, start, end),
        )
        return row["cnt"] if row else 0

    async def _check_brand_spacing(
        self, brand_id: str, platform: str, proposed: datetime
    ) -> bool:
        """Verify no other brand is scheduled within ±5 minutes on same platform."""
        lo = (proposed - timedelta(minutes=MIN_BRAND_SPACING_MINUTES)).isoformat()
        hi = (proposed + timedelta(minutes=MIN_BRAND_SPACING_MINUTES)).isoformat()

        row = await self.db.fetch_one(
            """
            SELECT COUNT(*) AS cnt FROM content_queue
            WHERE platform = ? AND brand_id != ?
                  AND scheduled_for >= ? AND scheduled_for <= ?
                  AND status = 'waiting'
            """,
            (platform, brand_id, lo, hi),
        )
        return (row["cnt"] if row else 0) == 0

    @staticmethod
    def _deterministic_offset(
        brand_id: str, platform: str, d: date, window_idx: int
    ) -> int:
        """Generate a deterministic ±30 minute offset.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        platform:
            Platform name.
        d:
            Target date.
        window_idx:
            Window index.

        Returns
        -------
        int
            Offset in minutes (−30 to +30).
        """
        seed = f"{brand_id}{platform}{d.isoformat()}{window_idx}"
        h = hashlib.md5(seed.encode()).hexdigest()
        return (int(h, 16) % 61) - 30
