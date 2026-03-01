"""
Brand Safety Scorer — evaluates scripts against brand guidelines BEFORE video assembly.

Uses Ollama (local, free LLM) via LLMRouter for evaluation.
Scoring range: 0-10.  Scripts scoring < 7.0 are rejected and must be regenerated.

Checks performed:
  1. Voice consistency (matches brand voice persona)
  2. Forbidden words (brand-specific blacklist)
  3. Tone alignment (brand tone guidelines)
  4. Pillar alignment (matches brand content pillars)
  5. CTA appropriateness

Results stored in the ``brand_safety_scores`` table.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("autofarm.brand.safety_scorer")

# ---------------------------------------------------------------------------
# Score result container
# ---------------------------------------------------------------------------

REJECTION_THRESHOLD = 7.0


@dataclass
class SafetyResult:
    """Encapsulates the outcome of a brand-safety evaluation."""

    script_id: int
    brand_id: str
    safety_score: float
    passed: bool
    issues: List[str] = field(default_factory=list)
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dictionary for storage / logging."""
        return {
            "script_id": self.script_id,
            "brand_id": self.brand_id,
            "safety_score": self.safety_score,
            "passed": self.passed,
            "issues": self.issues,
            "evaluated_at": self.evaluated_at.isoformat(),
        }


# ---------------------------------------------------------------------------
# BrandSafetyScorer
# ---------------------------------------------------------------------------


class BrandSafetyScorer:
    """Evaluate a script for brand-safety compliance.

    Parameters
    ----------
    db:
        Database helper (``database.db.Database`` instance).
    llm_router:
        ``LLMRouter`` instance — all LLM calls are routed through this.
    brands_config:
        Loaded ``config/brands.json`` data keyed by brand_id.
    """

    def __init__(
        self,
        db: Any,
        llm_router: Any,
        brands_config: Dict[str, Any],
    ) -> None:
        self.db = db
        self.llm_router = llm_router
        self.brands_config = brands_config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def score_script(
        self,
        script_id: int,
        brand_id: str,
        script_text: str,
    ) -> SafetyResult:
        """Run full safety evaluation on *script_text* for *brand_id*.

        Parameters
        ----------
        script_id:
            Primary key in the ``scripts`` table.
        brand_id:
            Brand identifier (must exist in ``brands_config``).
        script_text:
            The raw script body to evaluate.

        Returns
        -------
        SafetyResult
            Populated result; also persisted to the database.

        Side Effects
        ------------
        * Inserts a row into ``brand_safety_scores``.
        """
        brand = self.brands_config.get(brand_id, {})
        issues: List[str] = []

        # 1. Forbidden-word check (fast, no LLM needed) ----------------
        forbidden_issues = self._check_forbidden_words(script_text, brand)
        issues.extend(forbidden_issues)

        # 2. LLM-based evaluation --------------------------------------
        llm_score, llm_issues = await self._llm_evaluate(
            script_text, brand_id, brand
        )
        issues.extend(llm_issues)

        # 3. Compute composite score -----------------------------------
        #    Forbidden words hard-penalise; LLM score is primary signal.
        penalty = min(len(forbidden_issues) * 1.5, 4.0)
        final_score = round(max(llm_score - penalty, 0.0), 2)
        passed = final_score >= REJECTION_THRESHOLD

        result = SafetyResult(
            script_id=script_id,
            brand_id=brand_id,
            safety_score=final_score,
            passed=passed,
            issues=issues,
        )

        # 4. Persist to DB ---------------------------------------------
        await self._store_result(result)

        logger.info(
            "Brand safety score for script %s / brand %s: %.2f (%s)",
            script_id,
            brand_id,
            final_score,
            "PASS" if passed else "FAIL",
        )
        return result

    # ------------------------------------------------------------------
    # Forbidden-word check
    # ------------------------------------------------------------------

    def _check_forbidden_words(
        self, script_text: str, brand: Dict[str, Any]
    ) -> List[str]:
        """Return a list of issue strings for every forbidden word found.

        Parameters
        ----------
        script_text:
            The script body.
        brand:
            Brand config dict, expected to contain ``forbidden_words``.

        Returns
        -------
        List[str]
            One string per forbidden word found.
        """
        forbidden: List[str] = brand.get("forbidden_words", [])
        lower_text = script_text.lower()
        found: List[str] = []
        for word in forbidden:
            pattern = rf"\b{re.escape(word.lower())}\b"
            if re.search(pattern, lower_text):
                found.append(f"Forbidden word detected: '{word}'")
        return found

    # ------------------------------------------------------------------
    # LLM evaluation
    # ------------------------------------------------------------------

    async def _llm_evaluate(
        self,
        script_text: str,
        brand_id: str,
        brand: Dict[str, Any],
    ) -> tuple[float, List[str]]:
        """Ask the LLM to evaluate voice consistency, tone, pillar alignment, and CTA.

        Parameters
        ----------
        script_text:
            The raw script body.
        brand_id:
            Brand identifier.
        brand:
            Brand config dict.

        Returns
        -------
        tuple[float, List[str]]
            (score 0-10, list of issue descriptions).
        """
        voice = brand.get("voice", "neutral")
        tone = brand.get("tone", "informational")
        pillars = brand.get("content_pillars", [])
        niche = brand.get("niche", "general")

        prompt = (
            "You are a brand-safety evaluator. Score the following script on a "
            "0-10 scale based on these criteria:\n"
            f"  Brand voice: {voice}\n"
            f"  Brand tone: {tone}\n"
            f"  Content pillars: {', '.join(pillars) if pillars else 'N/A'}\n"
            f"  Niche: {niche}\n\n"
            "Evaluate:\n"
            "  1. Voice consistency — does the script match the brand voice?\n"
            "  2. Tone alignment — is the tone appropriate?\n"
            "  3. Pillar alignment — does the topic fit the brand pillars?\n"
            "  4. CTA appropriateness — is any call-to-action suitable?\n\n"
            "Respond ONLY with valid JSON:\n"
            '{"score": <float 0-10>, "issues": ["issue1", ...]}\n\n'
            f"SCRIPT:\n{script_text}"
        )

        try:
            raw = await self.llm_router.generate(
                prompt=prompt,
                task="brand_safety",
                brand_id=brand_id,
            )
            data = json.loads(raw)
            score = float(data.get("score", 5.0))
            issues = list(data.get("issues", []))
            return (min(max(score, 0.0), 10.0), issues)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.warning("LLM safety eval parse error: %s", exc)
            # Conservative fallback — do not auto-pass on error
            return (5.0, ["LLM evaluation failed; defaulting to conservative score"])

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    async def _store_result(self, result: SafetyResult) -> None:
        """Insert a row into ``brand_safety_scores``.

        Parameters
        ----------
        result:
            The evaluated safety result.

        Side Effects
        ------------
        Writes one row to the database.
        """
        await self.db.execute(
            """
            INSERT INTO brand_safety_scores
                (script_id, brand_id, safety_score, passed, issues, evaluated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                result.script_id,
                result.brand_id,
                result.safety_score,
                1 if result.passed else 0,
                json.dumps(result.issues),
                result.evaluated_at.isoformat(),
            ),
        )

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    async def get_history(
        self, brand_id: str, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Return recent safety-score records for a brand.

        Parameters
        ----------
        brand_id:
            Brand to query.
        limit:
            Max rows returned.

        Returns
        -------
        List[Dict[str, Any]]
            Rows as dicts.
        """
        rows = await self.db.fetch_all(
            """
            SELECT script_id, brand_id, safety_score, passed, issues, evaluated_at
            FROM brand_safety_scores
            WHERE brand_id = ?
            ORDER BY evaluated_at DESC
            LIMIT ?
            """,
            (brand_id, limit),
        )
        return [dict(r) for r in rows]
