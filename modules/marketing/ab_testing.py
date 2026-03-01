"""
A/B Hook Testing — every 10th video creates two variants with different
hook types.  Variant A publishes first, B follows 2 hours later.  After
48 hours the 3-second hold rates are compared and the winning hook type
receives a weight boost in ``hook_performance``.

Integration points:
- Triggered by ``Publisher`` (every 10th video).
- Hook variants generated via ``LLMRouter``.
- Results resolved by ``HookOptimizer.resolve_pending_ab_tests()``.
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("autofarm.marketing.ab_testing")

# Ratio: 1 in N videos becomes an A/B test
AB_TEST_RATIO = 10

# Delay between variant A and variant B publish
VARIANT_B_DELAY_HOURS = 2

# Hours to wait before resolving the test
RESOLUTION_DELAY_HOURS = 48

# All known hook types (must match hook_engine.py)
HOOK_TYPES: List[str] = [
    "question", "statistic", "story", "controversial",
    "command", "curiosity_gap", "pain_point", "social_proof",
]


class HookABTester:
    """Manage A/B hook testing lifecycle.

    Parameters
    ----------
    db:
        Database helper instance.
    llm_router:
        ``LLMRouter`` for generating hook variants.
    script_writer:
        ``ScriptWriter`` for building full scripts from hooks.
    """

    def __init__(
        self,
        db: Any,
        llm_router: Any,
        script_writer: Optional[Any] = None,
    ) -> None:
        self.db = db
        self.llm_router = llm_router
        self.script_writer = script_writer
        self._video_counter: Dict[str, int] = {}

    # ------------------------------------------------------------------
    # Should we run an A/B test?
    # ------------------------------------------------------------------

    async def should_ab_test(self, brand_id: str, platform: str) -> bool:
        """Determine if the next video should be an A/B test.

        Triggers on every Nth video per brand × platform, with N defined
        by ``AB_TEST_RATIO``.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        platform:
            Platform name.

        Returns
        -------
        bool
            ``True`` if the next video should be split-tested.
        """
        key = f"{brand_id}:{platform}"
        self._video_counter[key] = self._video_counter.get(key, 0) + 1

        if self._video_counter[key] >= AB_TEST_RATIO:
            self._video_counter[key] = 0
            # Double-check there isn't already an unresolved test
            pending = await self.db.fetch_one(
                """
                SELECT COUNT(*) AS cnt FROM ab_tests
                WHERE brand_id = ? AND platform = ? AND winner IS NULL
                """,
                (brand_id, platform),
            )
            if pending and pending["cnt"] >= 2:
                logger.info(
                    "Skipping A/B test for %s/%s — 2+ pending tests",
                    brand_id, platform,
                )
                return False
            return True
        return False

    # ------------------------------------------------------------------
    # Create A/B test
    # ------------------------------------------------------------------

    async def create_ab_test(
        self,
        brand_id: str,
        platform: str,
        original_script_id: int,
        trend_id: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Create an A/B test with two hook variants.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        platform:
            Platform name.
        original_script_id:
            The base script to create a variant of.
        trend_id:
            Optional trend the script is based on.

        Returns
        -------
        Optional[Dict[str, Any]]
            ``{test_id, variant_a_script_id, variant_b_script_id,
              hook_type_a, hook_type_b}`` or ``None`` on failure.

        Side Effects
        ------------
        Creates a new script (variant B) and an ``ab_tests`` row.
        """
        # Get the original script details
        original = await self.db.fetch_one(
            "SELECT * FROM scripts WHERE id = ?",
            (original_script_id,),
        )
        if not original:
            logger.error("Script %d not found for A/B test", original_script_id)
            return None

        hook_type_a = original["hook_type"] or "question"

        # Pick a different hook type for variant B
        hook_type_b = await self._pick_different_hook(
            brand_id, platform, hook_type_a
        )

        # Generate variant B script with different hook
        variant_b_script_id = await self._generate_variant(
            brand_id, original, hook_type_b, trend_id
        )
        if not variant_b_script_id:
            logger.warning("Failed to generate variant B for A/B test")
            return None

        # Insert A/B test record
        now = datetime.now(timezone.utc).isoformat()
        test_id = await self.db.execute(
            """
            INSERT INTO ab_tests
                (brand_id, platform,
                 variant_a_script_id, variant_b_script_id,
                 hook_type_a, hook_type_b, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                brand_id, platform,
                original_script_id, variant_b_script_id,
                hook_type_a, hook_type_b, now,
            ),
        )

        logger.info(
            "Created A/B test %d for %s/%s: %s vs %s",
            test_id, brand_id, platform, hook_type_a, hook_type_b,
        )
        return {
            "test_id": test_id,
            "variant_a_script_id": original_script_id,
            "variant_b_script_id": variant_b_script_id,
            "hook_type_a": hook_type_a,
            "hook_type_b": hook_type_b,
        }

    # ------------------------------------------------------------------
    # Link published jobs to test
    # ------------------------------------------------------------------

    async def link_publish_job(
        self,
        test_id: int,
        variant: str,
        publish_job_id: int,
    ) -> None:
        """Link a published job to its A/B test variant.

        Parameters
        ----------
        test_id:
            ``ab_tests.id``.
        variant:
            ``'A'`` or ``'B'``.
        publish_job_id:
            ``publish_jobs.id``.

        Side Effects
        ------------
        Updates the ``ab_tests`` row with the publish job ID.
        """
        column = (
            "variant_a_job_id" if variant == "A" else "variant_b_job_id"
        )
        await self.db.execute(
            f"UPDATE ab_tests SET {column} = ? WHERE id = ?",
            (publish_job_id, test_id),
        )

    # ------------------------------------------------------------------
    # Get schedule for variant B
    # ------------------------------------------------------------------

    def get_variant_b_schedule(
        self, variant_a_time: datetime
    ) -> datetime:
        """Calculate when variant B should be published.

        Parameters
        ----------
        variant_a_time:
            Scheduled time for variant A.

        Returns
        -------
        datetime
            Scheduled time for variant B (A + VARIANT_B_DELAY_HOURS).
        """
        return variant_a_time + timedelta(hours=VARIANT_B_DELAY_HOURS)

    # ------------------------------------------------------------------
    # Query tests
    # ------------------------------------------------------------------

    async def get_pending_tests(
        self, brand_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Return all unresolved A/B tests.

        Parameters
        ----------
        brand_id:
            Optional brand filter.

        Returns
        -------
        List[Dict[str, Any]]
            Pending test rows.
        """
        if brand_id:
            rows = await self.db.fetch_all(
                """
                SELECT * FROM ab_tests
                WHERE winner IS NULL AND brand_id = ?
                ORDER BY created_at DESC
                """,
                (brand_id,),
            )
        else:
            rows = await self.db.fetch_all(
                "SELECT * FROM ab_tests WHERE winner IS NULL ORDER BY created_at DESC"
            )
        return [dict(r) for r in rows]

    async def get_test_history(
        self,
        brand_id: str,
        platform: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Return resolved A/B tests for a brand.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        platform:
            Optional platform filter.
        limit:
            Maximum rows to return.

        Returns
        -------
        List[Dict[str, Any]]
            Resolved test rows with results.
        """
        params: list = [brand_id]
        plat_clause = ""
        if platform:
            plat_clause = " AND platform = ?"
            params.append(platform)
        params.append(limit)

        rows = await self.db.fetch_all(
            f"""
            SELECT * FROM ab_tests
            WHERE winner IS NOT NULL AND brand_id = ?{plat_clause}
            ORDER BY resolved_at DESC
            LIMIT ?
            """,
            tuple(params),
        )
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _pick_different_hook(
        self,
        brand_id: str,
        platform: str,
        exclude_type: str,
    ) -> str:
        """Choose a hook type different from the excluded one.

        Prefers under-tested types (low sample count) to maximise
        exploration.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        platform:
            Platform name.
        exclude_type:
            Hook type to exclude.

        Returns
        -------
        str
            Selected hook type.
        """
        # Get current performance data
        rows = await self.db.fetch_all(
            """
            SELECT hook_type, sample_count
            FROM hook_performance
            WHERE brand_id = ? AND platform = ?
            ORDER BY sample_count ASC
            """,
            (brand_id, platform),
        )

        tested_types = {r["hook_type"] for r in rows}
        # Prefer untested types first
        untested = [
            ht for ht in HOOK_TYPES
            if ht != exclude_type and ht not in tested_types
        ]
        if untested:
            return random.choice(untested)

        # Then prefer low-sample types
        candidates = [
            r["hook_type"] for r in rows
            if r["hook_type"] != exclude_type
        ]
        if candidates:
            return candidates[0]  # Already sorted by sample_count ASC

        # Fallback: random different type
        alternatives = [ht for ht in HOOK_TYPES if ht != exclude_type]
        return random.choice(alternatives) if alternatives else "question"

    async def _generate_variant(
        self,
        brand_id: str,
        original: Dict[str, Any],
        new_hook_type: str,
        trend_id: Optional[int],
    ) -> Optional[int]:
        """Generate a variant script with a different hook type.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        original:
            Original script row dict.
        new_hook_type:
            Hook type for the variant.
        trend_id:
            Optional trend ID.

        Returns
        -------
        Optional[int]
            New script ID or ``None`` on failure.

        Side Effects
        ------------
        Creates a new row in the ``scripts`` table.
        """
        prompt = (
            f"Rewrite this script with a '{new_hook_type}' style hook. "
            f"Keep the same topic, body structure, and CTA. "
            f"Only change the opening hook to use a {new_hook_type} approach.\n\n"
            f"Original hook: {original['hook']}\n"
            f"Original body: {original['body']}\n"
            f"Original CTA: {original['cta']}"
        )

        try:
            response = await self.llm_router.generate(
                prompt=prompt,
                task_type="script_variant",
                brand_id=brand_id,
            )

            # Parse the response — expect hook, body, cta sections
            text = response.get("text", "") if isinstance(response, dict) else str(response)

            # Simple extraction: use the LLM output as the new hook,
            # keep original body and CTA
            new_hook = text.strip()[:200] if text.strip() else original["hook"]
            body = original["body"]
            cta = original["cta"]
            script_text = f"{new_hook}\n\n{body}\n\n{cta}"
            word_count = len(script_text.split())

            now = datetime.now(timezone.utc).isoformat()
            script_id = await self.db.execute(
                """
                INSERT INTO scripts
                    (brand_id, trend_id, hook, hook_type, body, cta,
                     script_text, word_count, pillar, series_name,
                     series_number, llm_provider, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?)
                """,
                (
                    brand_id, trend_id, new_hook, new_hook_type,
                    body, cta, script_text, word_count,
                    original.get("pillar"), original.get("series_name"),
                    original.get("series_number"),
                    response.get("provider", "ollama") if isinstance(response, dict) else "ollama",
                    now,
                ),
            )
            return script_id

        except Exception as exc:
            logger.error("Failed to generate A/B variant: %s", exc)
            return None
