"""
Quality Gate — last check before the review queue.

Enforces minimum quality thresholds; content that fails is auto-rejected
and sent back to the AI Brain for regeneration.

Thresholds (all must pass):
  1. word_count    : 80–200 words
  2. hook          : first sentence ≤ 15 words
  3. sentences     : average ≤ 13 words per sentence
  4. brand_safety  : ≥ 7.0 (from BrandSafetyScorer)
  5. duration      : 30–62 seconds
  6. thumbnail_quality : ≥ 0.6

Rule #9 (Part 20): QualityGate.check() runs BEFORE ReviewGate.process().
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("autofarm.brand.quality_gate")

# ---------------------------------------------------------------------------
# Default thresholds (overridable via constructor)
# ---------------------------------------------------------------------------

DEFAULT_THRESHOLDS: Dict[str, Any] = {
    "min_word_count": 80,
    "max_word_count": 200,
    "max_hook_words": 15,
    "max_avg_sentence_words": 13,
    "min_brand_safety": 7.0,
    "min_duration": 30,
    "max_duration": 62,
    "min_thumbnail_quality": 0.6,
}


# ---------------------------------------------------------------------------
# Gate result container
# ---------------------------------------------------------------------------


@dataclass
class GateResult:
    """Outcome of the quality gate check."""

    passed: bool
    failures: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialise for storage / logging."""
        return {
            "passed": self.passed,
            "failures": self.failures,
            "metrics": self.metrics,
        }


# ---------------------------------------------------------------------------
# QualityGate
# ---------------------------------------------------------------------------


class QualityGate:
    """Final validation before human review.

    Parameters
    ----------
    thresholds:
        Optional dict to override any of the default thresholds.
    """

    def __init__(self, thresholds: Optional[Dict[str, Any]] = None) -> None:
        self.thresholds: Dict[str, Any] = {**DEFAULT_THRESHOLDS}
        if thresholds:
            self.thresholds.update(thresholds)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(
        self,
        script_text: str,
        brand_safety_score: float,
        duration_seconds: float,
        thumbnail_quality: float,
    ) -> GateResult:
        """Run all quality checks and return a consolidated result.

        Parameters
        ----------
        script_text:
            The final script body.
        brand_safety_score:
            Score from ``BrandSafetyScorer`` (0–10).
        duration_seconds:
            Video duration in seconds.
        thumbnail_quality:
            Thumbnail appeal score (0–1).

        Returns
        -------
        GateResult
            ``passed=True`` only if every threshold is met.
        """
        failures: List[str] = []
        metrics: Dict[str, Any] = {}

        # 1. Word count ------------------------------------------------
        word_count = self._word_count(script_text)
        metrics["word_count"] = word_count
        if word_count < self.thresholds["min_word_count"]:
            failures.append(
                f"Word count too low: {word_count} < {self.thresholds['min_word_count']}"
            )
        if word_count > self.thresholds["max_word_count"]:
            failures.append(
                f"Word count too high: {word_count} > {self.thresholds['max_word_count']}"
            )

        # 2. Hook length -----------------------------------------------
        hook_words = self._hook_word_count(script_text)
        metrics["hook_words"] = hook_words
        if hook_words > self.thresholds["max_hook_words"]:
            failures.append(
                f"Hook too long: {hook_words} words > {self.thresholds['max_hook_words']}"
            )

        # 3. Average sentence length -----------------------------------
        avg_sentence = self._avg_sentence_length(script_text)
        metrics["avg_sentence_words"] = avg_sentence
        if avg_sentence > self.thresholds["max_avg_sentence_words"]:
            failures.append(
                f"Avg sentence too long: {avg_sentence:.1f} words > "
                f"{self.thresholds['max_avg_sentence_words']}"
            )

        # 4. Brand safety score ----------------------------------------
        metrics["brand_safety_score"] = brand_safety_score
        if brand_safety_score < self.thresholds["min_brand_safety"]:
            failures.append(
                f"Brand safety too low: {brand_safety_score} < "
                f"{self.thresholds['min_brand_safety']}"
            )

        # 5. Duration --------------------------------------------------
        metrics["duration_seconds"] = duration_seconds
        if duration_seconds < self.thresholds["min_duration"]:
            failures.append(
                f"Duration too short: {duration_seconds}s < {self.thresholds['min_duration']}s"
            )
        if duration_seconds > self.thresholds["max_duration"]:
            failures.append(
                f"Duration too long: {duration_seconds}s > {self.thresholds['max_duration']}s"
            )

        # 6. Thumbnail quality -----------------------------------------
        metrics["thumbnail_quality"] = thumbnail_quality
        if thumbnail_quality < self.thresholds["min_thumbnail_quality"]:
            failures.append(
                f"Thumbnail quality too low: {thumbnail_quality:.2f} < "
                f"{self.thresholds['min_thumbnail_quality']}"
            )

        passed = len(failures) == 0
        result = GateResult(passed=passed, failures=failures, metrics=metrics)

        if passed:
            logger.info("Quality gate PASSED: %s", metrics)
        else:
            logger.warning(
                "Quality gate FAILED (%d issues): %s", len(failures), failures
            )

        return result

    # ------------------------------------------------------------------
    # Text analysis helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _word_count(text: str) -> int:
        """Return the number of words in *text*.

        Parameters
        ----------
        text:
            Input string.

        Returns
        -------
        int
        """
        return len(text.split())

    @staticmethod
    def _hook_word_count(text: str) -> int:
        """Return the word count of the first sentence.

        Parameters
        ----------
        text:
            Script body.

        Returns
        -------
        int
            Word count of the first sentence (delimited by '.', '!', or '?').
        """
        sentences = re.split(r"[.!?]+", text.strip())
        first = sentences[0].strip() if sentences else ""
        return len(first.split()) if first else 0

    @staticmethod
    def _avg_sentence_length(text: str) -> float:
        """Return the average number of words per sentence.

        Parameters
        ----------
        text:
            Script body.

        Returns
        -------
        float
            Average words per sentence; 0.0 if no sentences found.
        """
        sentences = [
            s.strip() for s in re.split(r"[.!?]+", text.strip()) if s.strip()
        ]
        if not sentences:
            return 0.0
        total_words = sum(len(s.split()) for s in sentences)
        return round(total_words / len(sentences), 2)
