"""
Engagement Scorer — analyses published-video analytics to compute CPS
(Content Performance Score), 3-second hold rates, retention, and
per-hook-type aggregate weights.

Results feed into ``HookOptimizer`` and ``ModelUpdater`` so the system
learns which hooks, pillars, and posting windows perform best.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("autofarm.feedback_loop.scorer")

# Weights for CPS sub-components (must sum to 1.0)
CPS_WEIGHTS: Dict[str, float] = {
    "three_second_hold_rate": 0.30,
    "retention_rate": 0.25,
    "engagement_rate": 0.25,
    "views_normalised": 0.20,
}

# Normalisation caps (prevents outlier domination)
VIEWS_CAP = 500_000  # views above this are capped for scoring
ENGAGEMENT_CAP = 0.15  # engagement rates above 15 % are capped


class EngagementScorer:
    """Analyse analytics rows and produce performance scores.

    Parameters
    ----------
    db:
        Database helper instance (``database.db.Database``).
    """

    def __init__(self, db: Any) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Score a single video
    # ------------------------------------------------------------------

    async def score_video_analytics(
        self, publish_job_id: int
    ) -> Dict[str, Any]:
        """Calculate CPS and all engagement metrics for a video.

        Parameters
        ----------
        publish_job_id:
            ``publish_jobs.id`` primary key.

        Returns
        -------
        Dict[str, Any]
            ``{cps_score, three_second_hold, retention_rate,
              engagement_rate, views, publish_job_id}``

        Side Effects
        ------------
        Updates ``analytics.cps_score`` in the database.
        """
        row = await self.db.fetch_one(
            """
            SELECT id, views, likes, comments, shares, saves,
                   watch_time_seconds, avg_view_duration_seconds,
                   retention_rate, three_second_hold_rate,
                   impressions, reach, engagement_rate
            FROM analytics
            WHERE publish_job_id = ?
            ORDER BY pulled_at DESC LIMIT 1
            """,
            (publish_job_id,),
        )
        if not row:
            logger.warning(
                "No analytics row for publish_job_id=%d", publish_job_id
            )
            return {
                "publish_job_id": publish_job_id,
                "cps_score": 0.0,
                "three_second_hold": 0.0,
                "retention_rate": 0.0,
                "engagement_rate": 0.0,
                "views": 0,
            }

        row = dict(row)
        three_sec = row.get("three_second_hold_rate", 0.0) or 0.0
        retention = row.get("retention_rate", 0.0) or 0.0
        views = row.get("views", 0) or 0
        impressions = row.get("impressions", 0) or 0
        likes = row.get("likes", 0) or 0
        comments = row.get("comments", 0) or 0
        shares = row.get("shares", 0) or 0
        saves = row.get("saves", 0) or 0

        # Engagement rate (fallback calculation if stored value is 0)
        eng_rate = row.get("engagement_rate", 0.0) or 0.0
        if eng_rate == 0.0 and impressions > 0:
            eng_rate = (likes + comments + shares + saves) / impressions

        # Normalise
        views_norm = min(views / VIEWS_CAP, 1.0) if VIEWS_CAP else 0.0
        eng_norm = min(eng_rate / ENGAGEMENT_CAP, 1.0) if ENGAGEMENT_CAP else 0.0

        cps = (
            CPS_WEIGHTS["three_second_hold_rate"] * three_sec
            + CPS_WEIGHTS["retention_rate"] * retention
            + CPS_WEIGHTS["engagement_rate"] * eng_norm
            + CPS_WEIGHTS["views_normalised"] * views_norm
        )
        # Scale to 0-10
        cps = round(cps * 10, 3)

        # Persist
        await self.db.execute(
            "UPDATE analytics SET cps_score = ?, engagement_rate = ? WHERE id = ?",
            (cps, round(eng_rate, 6), row["id"]),
        )

        return {
            "publish_job_id": publish_job_id,
            "cps_score": cps,
            "three_second_hold": three_sec,
            "retention_rate": retention,
            "engagement_rate": round(eng_rate, 6),
            "views": views,
        }

    # ------------------------------------------------------------------
    # Aggregate by hook type
    # ------------------------------------------------------------------

    async def aggregate_hook_performance(
        self,
        brand_id: str,
        hook_type: str,
        platform: str,
        lookback_days: int = 30,
    ) -> Dict[str, Any]:
        """Average performance for a specific hook type.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        hook_type:
            Hook classification string.
        platform:
            Platform name.
        lookback_days:
            Days of history to consider.

        Returns
        -------
        Dict[str, Any]
            ``{avg_three_second_hold, avg_retention_rate,
              avg_cps_score, sample_count}``
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=lookback_days)
        ).isoformat()

        rows = await self.db.fetch_all(
            """
            SELECT a.three_second_hold_rate, a.retention_rate, a.cps_score
            FROM analytics a
            JOIN publish_jobs pj ON pj.id = a.publish_job_id
            JOIN videos v ON v.id = pj.video_id
            JOIN scripts s ON s.id = v.script_id
            WHERE a.brand_id = ? AND a.platform = ?
                  AND s.hook_type = ? AND a.pulled_at >= ?
            """,
            (brand_id, platform, hook_type, cutoff),
        )

        if not rows:
            return {
                "avg_three_second_hold": 0.0,
                "avg_retention_rate": 0.0,
                "avg_cps_score": 0.0,
                "sample_count": 0,
            }

        n = len(rows)
        avg_hold = sum(r["three_second_hold_rate"] or 0 for r in rows) / n
        avg_ret = sum(r["retention_rate"] or 0 for r in rows) / n
        avg_cps = sum(r["cps_score"] or 0 for r in rows) / n

        return {
            "avg_three_second_hold": round(avg_hold, 4),
            "avg_retention_rate": round(avg_ret, 4),
            "avg_cps_score": round(avg_cps, 3),
            "sample_count": n,
        }

    # ------------------------------------------------------------------
    # Update hook weights
    # ------------------------------------------------------------------

    async def update_hook_weights(
        self, brand_id: str, platform: str, lookback_days: int = 30
    ) -> int:
        """Recalculate all hook-type weights for brand × platform.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        platform:
            Platform name.
        lookback_days:
            Days of history to consider.

        Returns
        -------
        int
            Number of hook types updated.

        Side Effects
        ------------
        Upserts ``hook_performance`` rows with normalised weights.
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=lookback_days)
        ).isoformat()

        # Group by hook_type
        rows = await self.db.fetch_all(
            """
            SELECT s.hook_type,
                   AVG(a.three_second_hold_rate) AS avg_hold,
                   AVG(a.retention_rate) AS avg_ret,
                   AVG(a.cps_score) AS avg_cps,
                   COUNT(*) AS cnt
            FROM analytics a
            JOIN publish_jobs pj ON pj.id = a.publish_job_id
            JOIN videos v ON v.id = pj.video_id
            JOIN scripts s ON s.id = v.script_id
            WHERE a.brand_id = ? AND a.platform = ? AND a.pulled_at >= ?
                  AND s.hook_type IS NOT NULL
            GROUP BY s.hook_type
            HAVING cnt >= 2
            """,
            (brand_id, platform, cutoff),
        )

        if not rows:
            return 0

        max_cps = max(r["avg_cps"] or 0.01 for r in rows) or 0.01
        now = datetime.now(timezone.utc).isoformat()
        updated = 0

        for r in rows:
            hook_type = r["hook_type"]
            avg_cps = r["avg_cps"] or 0.0
            weight = round(avg_cps / max_cps, 4) if max_cps else 1.0

            await self.db.execute(
                """
                INSERT INTO hook_performance
                    (brand_id, hook_type, platform,
                     avg_three_second_hold, avg_retention_rate,
                     avg_cps_score, sample_count, weight, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(brand_id, hook_type, platform) DO UPDATE SET
                    avg_three_second_hold = excluded.avg_three_second_hold,
                    avg_retention_rate = excluded.avg_retention_rate,
                    avg_cps_score = excluded.avg_cps_score,
                    sample_count = excluded.sample_count,
                    weight = excluded.weight,
                    last_updated = excluded.last_updated
                """,
                (
                    brand_id,
                    hook_type,
                    platform,
                    round(r["avg_hold"] or 0, 4),
                    round(r["avg_ret"] or 0, 4),
                    round(avg_cps, 3),
                    r["cnt"],
                    weight,
                    now,
                ),
            )
            updated += 1

        logger.info(
            "Updated %d hook weights for %s/%s", updated, brand_id, platform
        )
        return updated

    # ------------------------------------------------------------------
    # Batch scoring
    # ------------------------------------------------------------------

    async def score_recent_unscored(
        self, lookback_days: int = 7
    ) -> List[Dict[str, Any]]:
        """Score all analytics rows with ``cps_score = 0`` from the last N days.

        Parameters
        ----------
        lookback_days:
            Days of history to consider.

        Returns
        -------
        List[Dict[str, Any]]
            Scoring results for each row processed.

        Side Effects
        ------------
        Updates ``analytics.cps_score`` for each row.
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=lookback_days)
        ).isoformat()

        rows = await self.db.fetch_all(
            """
            SELECT DISTINCT publish_job_id
            FROM analytics
            WHERE cps_score = 0 AND pulled_at >= ?
            """,
            (cutoff,),
        )

        results: List[Dict[str, Any]] = []
        for row in rows:
            result = await self.score_video_analytics(row["publish_job_id"])
            results.append(result)

        logger.info("Scored %d unscored analytics records", len(results))
        return results

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    async def get_brand_performance_summary(
        self,
        brand_id: str,
        platform: Optional[str] = None,
        lookback_days: int = 30,
    ) -> Dict[str, Any]:
        """High-level performance summary for a brand.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        platform:
            Optional platform filter.
        lookback_days:
            Days of history.

        Returns
        -------
        Dict[str, Any]
            ``{total_videos, avg_cps, avg_views, avg_engagement_rate,
              best_hook_type, top_platforms}``
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=lookback_days)
        ).isoformat()

        params: list = [brand_id, cutoff]
        plat_clause = ""
        if platform:
            plat_clause = " AND a.platform = ?"
            params.append(platform)

        row = await self.db.fetch_one(
            f"""
            SELECT COUNT(*) AS total,
                   AVG(a.cps_score) AS avg_cps,
                   AVG(a.views) AS avg_views,
                   AVG(a.engagement_rate) AS avg_eng
            FROM analytics a
            WHERE a.brand_id = ? AND a.pulled_at >= ?{plat_clause}
            """,
            tuple(params),
        )

        best_hook = await self.db.fetch_one(
            """
            SELECT hook_type, weight
            FROM hook_performance
            WHERE brand_id = ? AND weight = (
                SELECT MAX(weight) FROM hook_performance WHERE brand_id = ?
            )
            LIMIT 1
            """,
            (brand_id, brand_id),
        )

        return {
            "brand_id": brand_id,
            "total_videos": row["total"] if row else 0,
            "avg_cps": round(row["avg_cps"] or 0, 3) if row else 0.0,
            "avg_views": round(row["avg_views"] or 0, 0) if row else 0,
            "avg_engagement_rate": round(row["avg_eng"] or 0, 5) if row else 0.0,
            "best_hook_type": best_hook["hook_type"] if best_hook else None,
        }
