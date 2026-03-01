"""
Hook Optimizer — uses scored analytics and A/B test results to adjust
hook-type weights per brand × platform.

The optimizer runs after ``EngagementScorer`` has scored recent videos
and is triggered by ``jobs/pull_analytics.py`` daily and by
``jobs/reoptimise_schedule.py`` weekly.

Hook weights are stored in ``hook_performance`` and consumed by
``modules/ai_brain/hook_engine.py`` when selecting hook types for new
scripts.  Higher weights → higher selection probability.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("autofarm.feedback_loop.hook_optimizer")

# Minimum samples before a hook type's weight is adjusted
MIN_SAMPLES = 3

# Exponential decay factor for older data (per day)
TIME_DECAY_FACTOR = 0.97

# Weight floor — never let a hook type drop below this
WEIGHT_FLOOR = 0.10

# Weight ceiling — cap to prevent monopoly
WEIGHT_CEILING = 2.0

# Lookback for recent performance (days)
DEFAULT_LOOKBACK_DAYS = 30

# Smoothing factor for EMA updates
EMA_ALPHA = 0.3

# All known hook types (from hook_engine.py)
HOOK_TYPES: List[str] = [
    "question", "statistic", "story", "controversial",
    "command", "curiosity_gap", "pain_point", "social_proof",
]


class HookOptimizer:
    """Analyse hook performance data and update selection weights.

    Parameters
    ----------
    db:
        Database helper instance (``database.db.Database``).
    scorer:
        ``EngagementScorer`` instance for recalculating scores if needed.
    """

    def __init__(self, db: Any, scorer: Optional[Any] = None) -> None:
        self.db = db
        self.scorer = scorer

    # ------------------------------------------------------------------
    # Primary entry
    # ------------------------------------------------------------------

    async def optimise_all(self) -> Dict[str, Any]:
        """Run hook optimization across all brand × platform combos.

        Returns
        -------
        Dict[str, Any]
            ``{brands_updated, hook_types_updated, ab_tests_resolved}``

        Side Effects
        ------------
        Updates ``hook_performance`` weights.
        Resolves pending ``ab_tests``.
        """
        brands = await self.db.fetch_all("SELECT id FROM brands")
        platforms = ["tiktok", "instagram", "facebook", "youtube", "snapchat"]

        total_hooks_updated = 0
        total_brands = 0
        ab_resolved = 0

        # Resolve A/B tests first — results feed into weight calculations
        ab_resolved = await self.resolve_pending_ab_tests()

        for brand in brands:
            brand_id = brand["id"]
            brand_updated = False

            for platform in platforms:
                updated = await self.optimise_hooks_for(
                    brand_id, platform
                )
                total_hooks_updated += updated
                if updated > 0:
                    brand_updated = True

            if brand_updated:
                total_brands += 1

        logger.info(
            "Hook optimization complete: %d brands, %d hook weights, "
            "%d A/B tests resolved",
            total_brands, total_hooks_updated, ab_resolved,
        )
        return {
            "brands_updated": total_brands,
            "hook_types_updated": total_hooks_updated,
            "ab_tests_resolved": ab_resolved,
        }

    # ------------------------------------------------------------------
    # Per-brand × platform optimization
    # ------------------------------------------------------------------

    async def optimise_hooks_for(
        self,
        brand_id: str,
        platform: str,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    ) -> int:
        """Recalculate hook weights for one brand × platform.

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
        Upserts ``hook_performance`` rows with new weights.
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=lookback_days)
        ).isoformat()

        # Get aggregated performance per hook type
        rows = await self.db.fetch_all(
            """
            SELECT s.hook_type,
                   a.cps_score,
                   a.three_second_hold_rate,
                   a.retention_rate,
                   a.pulled_at
            FROM analytics a
            JOIN publish_jobs pj ON pj.id = a.publish_job_id
            JOIN videos v ON v.id = pj.video_id
            JOIN scripts s ON s.id = v.script_id
            WHERE a.brand_id = ? AND a.platform = ?
                  AND a.pulled_at >= ?
                  AND s.hook_type IS NOT NULL
            ORDER BY a.pulled_at DESC
            """,
            (brand_id, platform, cutoff),
        )

        if not rows:
            return 0

        # Group by hook type and apply time decay
        hook_data: Dict[str, List[Tuple[float, float, float, float]]] = {}
        now = datetime.now(timezone.utc)

        for r in rows:
            ht = r["hook_type"]
            if ht not in hook_data:
                hook_data[ht] = []

            # Time decay: older data counts less
            try:
                pulled = datetime.fromisoformat(
                    r["pulled_at"].replace("Z", "+00:00")
                )
                if pulled.tzinfo is None:
                    pulled = pulled.replace(tzinfo=timezone.utc)
            except (ValueError, AttributeError):
                pulled = now

            days_ago = max((now - pulled).total_seconds() / 86400, 0)
            decay = TIME_DECAY_FACTOR ** days_ago

            hook_data[ht].append((
                (r["cps_score"] or 0) * decay,
                (r["three_second_hold_rate"] or 0) * decay,
                (r["retention_rate"] or 0) * decay,
                decay,  # Weight accumulator
            ))

        # Calculate weighted averages per hook type
        hook_scores: Dict[str, Dict[str, float]] = {}
        for ht, samples in hook_data.items():
            if len(samples) < MIN_SAMPLES:
                continue

            total_decay = sum(s[3] for s in samples)
            if total_decay == 0:
                continue

            hook_scores[ht] = {
                "avg_cps": sum(s[0] for s in samples) / total_decay,
                "avg_hold": sum(s[1] for s in samples) / total_decay,
                "avg_retention": sum(s[2] for s in samples) / total_decay,
                "sample_count": len(samples),
            }

        if not hook_scores:
            return 0

        # Normalise weights: best hook → 1.0, others proportional
        max_cps = max(hs["avg_cps"] for hs in hook_scores.values()) or 0.01
        now_iso = datetime.now(timezone.utc).isoformat()
        updated = 0

        for ht, stats in hook_scores.items():
            raw_weight = stats["avg_cps"] / max_cps if max_cps > 0 else 1.0

            # Apply EMA with existing weight if available
            existing = await self.db.fetch_one(
                """
                SELECT weight FROM hook_performance
                WHERE brand_id = ? AND hook_type = ? AND platform = ?
                """,
                (brand_id, ht, platform),
            )
            if existing and existing["weight"]:
                new_weight = (
                    EMA_ALPHA * raw_weight
                    + (1 - EMA_ALPHA) * existing["weight"]
                )
            else:
                new_weight = raw_weight

            # Clamp
            new_weight = max(WEIGHT_FLOOR, min(WEIGHT_CEILING, new_weight))
            new_weight = round(new_weight, 4)

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
                    brand_id, ht, platform,
                    round(stats["avg_hold"], 4),
                    round(stats["avg_retention"], 4),
                    round(stats["avg_cps"], 3),
                    stats["sample_count"],
                    new_weight,
                    now_iso,
                ),
            )
            updated += 1

        if updated:
            logger.info(
                "Updated %d hook weights for %s/%s", updated, brand_id, platform
            )
        return updated

    # ------------------------------------------------------------------
    # A/B test resolution
    # ------------------------------------------------------------------

    async def resolve_pending_ab_tests(
        self, min_age_hours: int = 48
    ) -> int:
        """Resolve A/B tests that are at least ``min_age_hours`` old.

        Compares 3-second hold rate between variant A and B.  The winner's
        hook type gets a weight boost; the loser gets a penalty.

        Parameters
        ----------
        min_age_hours:
            Minimum hours since the A/B test was created before resolving.

        Returns
        -------
        int
            Number of tests resolved.

        Side Effects
        ------------
        Updates ``ab_tests.winner``, ``ab_tests.resolved_at``,
        ``ab_tests.result_metric``.  Adjusts ``hook_performance.weight``.
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=min_age_hours)
        ).isoformat()

        tests = await self.db.fetch_all(
            """
            SELECT id, brand_id, platform,
                   variant_a_job_id, variant_b_job_id,
                   hook_type_a, hook_type_b
            FROM ab_tests
            WHERE winner IS NULL AND created_at <= ?
            """,
            (cutoff,),
        )

        resolved = 0
        for test in tests:
            result = await self._resolve_single_ab_test(test)
            if result:
                resolved += 1

        if resolved:
            logger.info("Resolved %d A/B tests", resolved)
        return resolved

    async def _resolve_single_ab_test(
        self, test: Dict[str, Any]
    ) -> bool:
        """Resolve a single A/B test and update hook weights.

        Parameters
        ----------
        test:
            Row from ``ab_tests`` table.

        Returns
        -------
        bool
            Whether the test was successfully resolved.

        Side Effects
        ------------
        Updates the ``ab_tests`` row and adjusts ``hook_performance`` weights.
        """
        # Fetch analytics for both variants
        a_analytics = await self.db.fetch_one(
            """
            SELECT three_second_hold_rate, cps_score, views
            FROM analytics
            WHERE publish_job_id = ?
            ORDER BY pulled_at DESC LIMIT 1
            """,
            (test["variant_a_job_id"],),
        )
        b_analytics = await self.db.fetch_one(
            """
            SELECT three_second_hold_rate, cps_score, views
            FROM analytics
            WHERE publish_job_id = ?
            ORDER BY pulled_at DESC LIMIT 1
            """,
            (test["variant_b_job_id"],),
        )

        if not a_analytics or not b_analytics:
            return False

        # Compare primarily on 3-second hold rate
        hold_a = a_analytics["three_second_hold_rate"] or 0
        hold_b = b_analytics["three_second_hold_rate"] or 0
        cps_a = a_analytics["cps_score"] or 0
        cps_b = b_analytics["cps_score"] or 0

        # Combined score: 60% hold rate, 40% CPS
        score_a = 0.6 * hold_a + 0.4 * (cps_a / 10.0)
        score_b = 0.6 * hold_b + 0.4 * (cps_b / 10.0)

        if score_a >= score_b:
            winner = "A"
            winner_hook = test["hook_type_a"]
            loser_hook = test["hook_type_b"]
        else:
            winner = "B"
            winner_hook = test["hook_type_b"]
            loser_hook = test["hook_type_a"]

        margin = abs(score_a - score_b)
        result_metric = (
            f"hold_a={hold_a:.3f} hold_b={hold_b:.3f} "
            f"cps_a={cps_a:.2f} cps_b={cps_b:.2f} margin={margin:.4f}"
        )
        now = datetime.now(timezone.utc).isoformat()

        # Update test record
        await self.db.execute(
            """
            UPDATE ab_tests
            SET winner = ?, result_metric = ?, resolved_at = ?
            WHERE id = ?
            """,
            (winner, result_metric, now, test["id"]),
        )

        # Boost winner, penalise loser
        boost = min(0.15, margin * 2)  # Proportional to victory margin
        brand_id = test["brand_id"]
        platform = test["platform"]

        if winner_hook:
            await self._adjust_weight(brand_id, platform, winner_hook, boost)
        if loser_hook:
            await self._adjust_weight(brand_id, platform, loser_hook, -boost)

        logger.info(
            "A/B test %d resolved: winner=%s (%s) margin=%.4f",
            test["id"], winner, winner_hook, margin,
        )
        return True

    async def _adjust_weight(
        self,
        brand_id: str,
        platform: str,
        hook_type: str,
        delta: float,
    ) -> None:
        """Apply a weight adjustment to a hook type.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        platform:
            Platform name.
        hook_type:
            Hook classification string.
        delta:
            Weight change (positive = boost, negative = penalty).

        Side Effects
        ------------
        Updates ``hook_performance.weight``, clamped to [WEIGHT_FLOOR, WEIGHT_CEILING].
        """
        row = await self.db.fetch_one(
            """
            SELECT weight FROM hook_performance
            WHERE brand_id = ? AND hook_type = ? AND platform = ?
            """,
            (brand_id, hook_type, platform),
        )

        current = row["weight"] if row else 1.0
        new_weight = max(WEIGHT_FLOOR, min(WEIGHT_CEILING, current + delta))
        now = datetime.now(timezone.utc).isoformat()

        await self.db.execute(
            """
            INSERT INTO hook_performance
                (brand_id, hook_type, platform, weight, last_updated)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(brand_id, hook_type, platform) DO UPDATE SET
                weight = excluded.weight,
                last_updated = excluded.last_updated
            """,
            (brand_id, hook_type, platform, round(new_weight, 4), now),
        )

    # ------------------------------------------------------------------
    # Weight queries
    # ------------------------------------------------------------------

    async def get_hook_weights(
        self, brand_id: str, platform: str
    ) -> Dict[str, float]:
        """Return current hook weights for hook selection.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        platform:
            Platform name.

        Returns
        -------
        Dict[str, float]
            ``{hook_type: weight}`` for all known types.
            Unknown / low-sample types get weight 1.0.
        """
        rows = await self.db.fetch_all(
            """
            SELECT hook_type, weight
            FROM hook_performance
            WHERE brand_id = ? AND platform = ?
            """,
            (brand_id, platform),
        )

        weights: Dict[str, float] = {ht: 1.0 for ht in HOOK_TYPES}
        for r in rows:
            if r["hook_type"] in weights:
                weights[r["hook_type"]] = r["weight"]

        return weights

    async def get_top_hooks(
        self, brand_id: str, platform: str, n: int = 3
    ) -> List[Dict[str, Any]]:
        """Return the top N performing hook types.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        platform:
            Platform name.
        n:
            Number of top hooks to return.

        Returns
        -------
        List[Dict[str, Any]]
            Sorted list with ``{hook_type, weight, avg_cps_score, sample_count}``.
        """
        rows = await self.db.fetch_all(
            """
            SELECT hook_type, weight, avg_cps_score, sample_count
            FROM hook_performance
            WHERE brand_id = ? AND platform = ?
            ORDER BY weight DESC
            LIMIT ?
            """,
            (brand_id, platform, n),
        )
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    async def get_optimization_report(
        self, brand_id: str
    ) -> Dict[str, Any]:
        """Generate a summary report of hook performance for a brand.

        Parameters
        ----------
        brand_id:
            Brand identifier.

        Returns
        -------
        Dict[str, Any]
            ``{brand_id, platforms: {platform: {hooks: [...], best_hook}}}``
        """
        platforms = ["tiktok", "instagram", "facebook", "youtube", "snapchat"]
        report: Dict[str, Any] = {"brand_id": brand_id, "platforms": {}}

        for platform in platforms:
            rows = await self.db.fetch_all(
                """
                SELECT hook_type, weight, avg_cps_score,
                       avg_three_second_hold, sample_count
                FROM hook_performance
                WHERE brand_id = ? AND platform = ?
                ORDER BY weight DESC
                """,
                (brand_id, platform),
            )

            hooks = [dict(r) for r in rows]
            best = hooks[0]["hook_type"] if hooks else None

            report["platforms"][platform] = {
                "hooks": hooks,
                "best_hook": best,
                "total_samples": sum(h.get("sample_count", 0) for h in hooks),
            }

        return report
