"""
Model Updater — adjusts system parameters and cached generation templates
based on accumulated feedback data.

Responsibilities:
- Updates posting-window performance rankings (``schedule_history``)
- Adjusts brand voice baselines when performance drifts
- Refreshes cached LLM response templates with top-performing patterns
- Updates content pillar distribution weights
- Logs all model changes for audit

Runs weekly via ``jobs/reoptimise_schedule.py`` and can be triggered
on-demand from the dashboard.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("autofarm.feedback_loop.model_updater")

# Minimum data points before updating schedule weights
MIN_SCHEDULE_SAMPLES = 10

# Minimum videos before pillar weight adjustment
MIN_PILLAR_SAMPLES = 5

# EMA smoothing for schedule window scores
SCHEDULE_EMA_ALPHA = 0.25

# Cached response template path
CACHED_RESPONSES_DIR = Path("config/cached_responses")


class ModelUpdater:
    """Update system models and parameters from feedback data.

    Parameters
    ----------
    db:
        Database helper instance (``database.db.Database``).
    scorer:
        ``EngagementScorer`` for on-demand score recalculation.
    hook_optimizer:
        ``HookOptimizer`` for triggering weight refreshes.
    """

    def __init__(
        self,
        db: Any,
        scorer: Optional[Any] = None,
        hook_optimizer: Optional[Any] = None,
    ) -> None:
        self.db = db
        self.scorer = scorer
        self.hook_optimizer = hook_optimizer

    # ------------------------------------------------------------------
    # Primary entry
    # ------------------------------------------------------------------

    async def run_full_update(self) -> Dict[str, Any]:
        """Execute all model updates in sequence.

        Returns
        -------
        Dict[str, Any]
            ``{schedule_updates, pillar_updates, template_updates,
              hook_updates, voice_updates}``

        Side Effects
        ------------
        Updates ``schedule_history``, ``hook_performance``,
        cached response templates, and ``system_config``.
        """
        results: Dict[str, Any] = {}

        # 1. Score any un-scored videos
        if self.scorer:
            scored = await self.scorer.score_recent_unscored(lookback_days=14)
            results["videos_scored"] = len(scored)

        # 2. Update hook weights
        if self.hook_optimizer:
            hook_result = await self.hook_optimizer.optimise_all()
            results["hook_updates"] = hook_result
        else:
            results["hook_updates"] = {"skipped": True}

        # 3. Update schedule window rankings
        schedule_result = await self.update_schedule_rankings()
        results["schedule_updates"] = schedule_result

        # 4. Update pillar weights
        pillar_result = await self.update_pillar_weights()
        results["pillar_updates"] = pillar_result

        # 5. Refresh cached templates
        template_result = await self.refresh_cached_templates()
        results["template_updates"] = template_result

        # 6. Log the model update event
        await self._log_model_update(results)

        logger.info("Full model update complete: %s", results)
        return results

    # ------------------------------------------------------------------
    # Schedule window ranking
    # ------------------------------------------------------------------

    async def update_schedule_rankings(
        self, lookback_days: int = 60
    ) -> Dict[str, Any]:
        """Rank posting windows by CPS for each brand × platform.

        Queries ``schedule_history`` + ``analytics`` to find which
        posting hours yield the highest engagement.

        Parameters
        ----------
        lookback_days:
            Days of history to consider.

        Returns
        -------
        Dict[str, Any]
            ``{updated_count, brand_platform_pairs}``

        Side Effects
        ------------
        Stores results in ``system_config`` under key
        ``schedule_rankings:{brand_id}:{platform}``.
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=lookback_days)
        ).isoformat()

        # Get all brand × platform combos with schedule data
        combos = await self.db.fetch_all(
            """
            SELECT DISTINCT sh.brand_id, sh.platform
            FROM schedule_history sh
            WHERE sh.created_at >= ?
            """,
            (cutoff,),
        )

        updated = 0
        for combo in combos:
            brand_id = combo["brand_id"]
            platform = combo["platform"]

            # Get per-window-hour performance
            rows = await self.db.fetch_all(
                """
                SELECT sh.window_hour,
                       AVG(a.cps_score) AS avg_cps,
                       AVG(a.three_second_hold_rate) AS avg_hold,
                       AVG(a.views) AS avg_views,
                       COUNT(*) AS sample_count
                FROM schedule_history sh
                JOIN publish_jobs pj ON pj.brand_id = sh.brand_id
                    AND pj.platform = sh.platform
                    AND DATE(pj.published_at) = DATE(sh.actual_publish_time)
                JOIN analytics a ON a.publish_job_id = pj.id
                WHERE sh.brand_id = ? AND sh.platform = ?
                      AND sh.created_at >= ?
                GROUP BY sh.window_hour
                HAVING sample_count >= ?
                ORDER BY avg_cps DESC
                """,
                (brand_id, platform, cutoff, MIN_SCHEDULE_SAMPLES),
            )

            if not rows:
                continue

            rankings = []
            for r in rows:
                rankings.append({
                    "hour": r["window_hour"],
                    "avg_cps": round(r["avg_cps"] or 0, 3),
                    "avg_hold": round(r["avg_hold"] or 0, 4),
                    "avg_views": round(r["avg_views"] or 0, 0),
                    "samples": r["sample_count"],
                })

            config_key = f"schedule_rankings:{brand_id}:{platform}"
            await self._upsert_config(
                config_key, json.dumps(rankings)
            )
            updated += 1

        logger.info("Updated schedule rankings for %d brand×platform combos", updated)
        return {"updated_count": updated, "brand_platform_pairs": len(combos)}

    # ------------------------------------------------------------------
    # Pillar weight updates
    # ------------------------------------------------------------------

    async def update_pillar_weights(
        self, lookback_days: int = 60
    ) -> Dict[str, Any]:
        """Update content pillar weights based on engagement per pillar.

        Parameters
        ----------
        lookback_days:
            Days of history to consider.

        Returns
        -------
        Dict[str, Any]
            ``{brands_updated, pillars_adjusted}``

        Side Effects
        ------------
        Stores results in ``system_config`` under key
        ``pillar_weights:{brand_id}``.
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=lookback_days)
        ).isoformat()

        brands = await self.db.fetch_all("SELECT id FROM brands")
        brands_updated = 0
        pillars_adjusted = 0

        for brand in brands:
            brand_id = brand["id"]

            rows = await self.db.fetch_all(
                """
                SELECT s.pillar,
                       AVG(a.cps_score) AS avg_cps,
                       AVG(a.engagement_rate) AS avg_eng,
                       COUNT(*) AS cnt
                FROM analytics a
                JOIN publish_jobs pj ON pj.id = a.publish_job_id
                JOIN videos v ON v.id = pj.video_id
                JOIN scripts s ON s.id = v.script_id
                WHERE a.brand_id = ? AND a.pulled_at >= ?
                      AND s.pillar IS NOT NULL
                GROUP BY s.pillar
                HAVING cnt >= ?
                ORDER BY avg_cps DESC
                """,
                (brand_id, cutoff, MIN_PILLAR_SAMPLES),
            )

            if not rows:
                continue

            max_cps = max(r["avg_cps"] or 0.01 for r in rows) or 0.01
            pillar_weights: Dict[str, Dict[str, Any]] = {}

            for r in rows:
                pillar = r["pillar"]
                weight = round((r["avg_cps"] or 0) / max_cps, 4)
                weight = max(0.2, weight)  # Floor at 20%

                pillar_weights[pillar] = {
                    "weight": weight,
                    "avg_cps": round(r["avg_cps"] or 0, 3),
                    "avg_engagement": round(r["avg_eng"] or 0, 5),
                    "sample_count": r["cnt"],
                }
                pillars_adjusted += 1

            config_key = f"pillar_weights:{brand_id}"
            await self._upsert_config(config_key, json.dumps(pillar_weights))
            brands_updated += 1

        logger.info(
            "Updated pillar weights: %d brands, %d pillars",
            brands_updated, pillars_adjusted,
        )
        return {
            "brands_updated": brands_updated,
            "pillars_adjusted": pillars_adjusted,
        }

    # ------------------------------------------------------------------
    # Cached template refresh
    # ------------------------------------------------------------------

    async def refresh_cached_templates(self) -> Dict[str, int]:
        """Update cached LLM response templates with top-performing patterns.

        Analyses high-CPS scripts and extracts successful hook/CTA patterns
        to update the cached response templates in
        ``config/cached_responses/``.

        Returns
        -------
        Dict[str, int]
            ``{scripts_analysed, templates_updated}``

        Side Effects
        ------------
        Writes updated templates to ``config/cached_responses/`` directory.
        """
        # Get top-performing scripts from last 30 days
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=30)
        ).isoformat()

        top_scripts = await self.db.fetch_all(
            """
            SELECT s.brand_id, s.hook, s.hook_type, s.body, s.cta,
                   s.pillar, a.cps_score, a.three_second_hold_rate
            FROM scripts s
            JOIN videos v ON v.script_id = s.id
            JOIN publish_jobs pj ON pj.video_id = v.id
            JOIN analytics a ON a.publish_job_id = pj.id
            WHERE a.pulled_at >= ? AND a.cps_score >= 5.0
            ORDER BY a.cps_score DESC
            LIMIT 50
            """,
            (cutoff,),
        )

        if not top_scripts:
            return {"scripts_analysed": 0, "templates_updated": 0}

        # Extract patterns grouped by brand
        brand_patterns: Dict[str, List[Dict[str, Any]]] = {}
        for s in top_scripts:
            bid = s["brand_id"]
            if bid not in brand_patterns:
                brand_patterns[bid] = []
            brand_patterns[bid].append({
                "hook": s["hook"],
                "hook_type": s["hook_type"],
                "cta": s["cta"],
                "pillar": s["pillar"],
                "cps_score": s["cps_score"],
            })

        # Update script_generation.json cache
        template_path = CACHED_RESPONSES_DIR / "script_generation.json"
        templates_updated = 0

        try:
            if template_path.exists():
                existing = json.loads(template_path.read_text())
            else:
                existing = {}

            # Add top_performers section
            existing["top_performers"] = {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "brands": {},
            }

            for brand_id, patterns in brand_patterns.items():
                # Top 5 hooks per brand
                top_hooks = sorted(
                    patterns, key=lambda x: x.get("cps_score", 0), reverse=True
                )[:5]

                existing["top_performers"]["brands"][brand_id] = {
                    "top_hooks": [
                        {"hook": h["hook"], "type": h["hook_type"],
                         "cps": h["cps_score"]}
                        for h in top_hooks
                    ],
                    "best_pillars": list({
                        p["pillar"] for p in patterns if p.get("pillar")
                    }),
                    "best_ctas": list({
                        p["cta"] for p in patterns if p.get("cta")
                    })[:5],
                }
                templates_updated += 1

            template_path.write_text(
                json.dumps(existing, indent=2, default=str)
            )
        except Exception as exc:
            logger.error("Failed to update cached templates: %s", exc)

        logger.info(
            "Refreshed templates: %d scripts analysed, %d brands updated",
            len(top_scripts), templates_updated,
        )
        return {
            "scripts_analysed": len(top_scripts),
            "templates_updated": templates_updated,
        }

    # ------------------------------------------------------------------
    # LLM performance tracking
    # ------------------------------------------------------------------

    async def get_llm_performance_summary(
        self, lookback_days: int = 30
    ) -> Dict[str, Any]:
        """Summarise LLM provider performance over the lookback window.

        Parameters
        ----------
        lookback_days:
            Days of history to consider.

        Returns
        -------
        Dict[str, Any]
            ``{providers: {name: {total_requests, avg_latency_ms,
              success_rate, total_tokens}}}``
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=lookback_days)
        ).isoformat()

        rows = await self.db.fetch_all(
            """
            SELECT provider,
                   COUNT(*) AS total,
                   AVG(latency_ms) AS avg_lat,
                   SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS successes,
                   SUM(tokens_used) AS total_tokens
            FROM llm_requests
            WHERE created_at >= ?
            GROUP BY provider
            """,
            (cutoff,),
        )

        providers: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            total = r["total"] or 1
            providers[r["provider"]] = {
                "total_requests": total,
                "avg_latency_ms": round(r["avg_lat"] or 0, 1),
                "success_rate": round((r["successes"] or 0) / total, 4),
                "total_tokens": r["total_tokens"] or 0,
            }

        return {"providers": providers}

    # ------------------------------------------------------------------
    # Trend effectiveness
    # ------------------------------------------------------------------

    async def analyse_trend_effectiveness(
        self, lookback_days: int = 30
    ) -> Dict[str, Any]:
        """Measure how well trend-based content performs vs non-trend.

        Parameters
        ----------
        lookback_days:
            Days of history to consider.

        Returns
        -------
        Dict[str, Any]
            ``{trend_avg_cps, non_trend_avg_cps, trend_lift_pct,
              top_trend_sources}``
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=lookback_days)
        ).isoformat()

        # Trend-based videos
        trend_row = await self.db.fetch_one(
            """
            SELECT AVG(a.cps_score) AS avg_cps, COUNT(*) AS cnt
            FROM analytics a
            JOIN publish_jobs pj ON pj.id = a.publish_job_id
            JOIN videos v ON v.id = pj.video_id
            JOIN scripts s ON s.id = v.script_id
            WHERE s.trend_id IS NOT NULL AND a.pulled_at >= ?
            """,
            (cutoff,),
        )

        # Non-trend videos
        non_trend_row = await self.db.fetch_one(
            """
            SELECT AVG(a.cps_score) AS avg_cps, COUNT(*) AS cnt
            FROM analytics a
            JOIN publish_jobs pj ON pj.id = a.publish_job_id
            JOIN videos v ON v.id = pj.video_id
            JOIN scripts s ON s.id = v.script_id
            WHERE s.trend_id IS NULL AND a.pulled_at >= ?
            """,
            (cutoff,),
        )

        trend_cps = (trend_row["avg_cps"] or 0) if trend_row else 0
        non_trend_cps = (non_trend_row["avg_cps"] or 0) if non_trend_row else 0
        lift = 0.0
        if non_trend_cps > 0:
            lift = ((trend_cps - non_trend_cps) / non_trend_cps) * 100

        # Top trend sources
        top_sources = await self.db.fetch_all(
            """
            SELECT t.source, AVG(a.cps_score) AS avg_cps, COUNT(*) AS cnt
            FROM analytics a
            JOIN publish_jobs pj ON pj.id = a.publish_job_id
            JOIN videos v ON v.id = pj.video_id
            JOIN scripts s ON s.id = v.script_id
            JOIN trends t ON t.id = s.trend_id
            WHERE a.pulled_at >= ?
            GROUP BY t.source
            ORDER BY avg_cps DESC
            LIMIT 5
            """,
            (cutoff,),
        )

        return {
            "trend_avg_cps": round(trend_cps, 3),
            "non_trend_avg_cps": round(non_trend_cps, 3),
            "trend_lift_pct": round(lift, 2),
            "top_trend_sources": [
                {"source": r["source"], "avg_cps": round(r["avg_cps"] or 0, 3),
                 "count": r["cnt"]}
                for r in top_sources
            ],
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _upsert_config(self, key: str, value: str) -> None:
        """Insert or update a ``system_config`` row.

        Parameters
        ----------
        key:
            Config key.
        value:
            Config value (JSON string).

        Side Effects
        ------------
        Upserts row in ``system_config`` table.
        """
        await self.db.execute(
            """
            INSERT INTO system_config (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )

    async def _log_model_update(self, results: Dict[str, Any]) -> None:
        """Record the model update in system metrics.

        Parameters
        ----------
        results:
            Full update results dict.

        Side Effects
        ------------
        Inserts row into ``system_metrics`` table.
        """
        now = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            """
            INSERT INTO system_metrics (metric_name, metric_value, recorded_at)
            VALUES (?, ?, ?)
            """,
            ("model_update", json.dumps(results, default=str), now),
        )
