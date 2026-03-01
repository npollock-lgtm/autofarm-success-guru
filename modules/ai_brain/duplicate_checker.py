"""
Duplicate content checker for AutoFarm Zero — Success Guru Network v6.0.

Checks scripts for duplication within a brand (same brand, repeated topics)
and across brands (cross-brand similarity). Works alongside the
CrossBrandDeduplicator but focuses on within-brand duplicate detection.

Prevents:
- Same topic being scripted twice for the same brand
- Very similar hooks appearing too close together
- Recycled content within a rolling window
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog

from database.db import Database

logger = structlog.get_logger(__name__)


class DuplicateChecker:
    """
    Checks for duplicate content within and across brands.

    Uses both exact matching (topic strings) and fuzzy matching
    (text similarity) to detect content that is too similar to
    recently published or queued content.

    Attributes:
        ROLLING_WINDOW_DAYS: Days of history to check against.
        TOPIC_SIMILARITY_THRESHOLD: Minimum topic overlap to flag.
        HOOK_SIMILARITY_THRESHOLD: Minimum hook text similarity to flag.
    """

    ROLLING_WINDOW_DAYS: int = 30
    TOPIC_SIMILARITY_THRESHOLD: float = 0.8
    HOOK_SIMILARITY_THRESHOLD: float = 0.7

    def __init__(self) -> None:
        """
        Initializes the DuplicateChecker.

        Side effects:
            Creates a Database instance.
        """
        self.db = Database()

    def check_topic_duplicate(self, brand_id: str,
                                topic: str) -> dict:
        """
        Checks if a topic has already been used for this brand recently.

        Parameters:
            brand_id: Brand identifier.
            topic: The topic to check.

        Returns:
            Dict with is_duplicate (bool), similar_script_id (optional),
            similarity_score, and reason.

        Side effects:
            Queries the scripts and trends tables.
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(
                days=self.ROLLING_WINDOW_DAYS)
        ).isoformat()

        # Exact topic match
        exact = self.db.fetch_one(
            "SELECT s.id, s.hook, t.topic FROM scripts s "
            "LEFT JOIN trends t ON s.trend_id = t.id "
            "WHERE s.brand_id=? AND t.topic=? AND s.created_at > ?",
            (brand_id, topic, cutoff)
        )

        if exact:
            return {
                'is_duplicate': True,
                'similar_script_id': exact['id'],
                'similarity_score': 1.0,
                'reason': f"Exact topic match with script #{exact['id']}",
            }

        # Partial match: check if topic words overlap significantly
        topic_words = set(topic.lower().split())
        if len(topic_words) < 3:
            return {
                'is_duplicate': False,
                'similar_script_id': None,
                'similarity_score': 0.0,
                'reason': 'Topic too short for similarity check',
            }

        recent_scripts = self.db.fetch_all(
            "SELECT s.id, s.hook, t.topic FROM scripts s "
            "LEFT JOIN trends t ON s.trend_id = t.id "
            "WHERE s.brand_id=? AND s.created_at > ? "
            "ORDER BY s.created_at DESC LIMIT 50",
            (brand_id, cutoff)
        )

        best_match = None
        best_score = 0.0

        for script in recent_scripts:
            existing_topic = script.get('topic') or script.get('hook') or ''
            existing_words = set(existing_topic.lower().split())

            if not existing_words:
                continue

            # Jaccard similarity
            intersection = topic_words & existing_words
            union = topic_words | existing_words
            similarity = len(intersection) / len(union) if union else 0

            if similarity > best_score:
                best_score = similarity
                best_match = script

        if best_score >= self.TOPIC_SIMILARITY_THRESHOLD and best_match:
            return {
                'is_duplicate': True,
                'similar_script_id': best_match['id'],
                'similarity_score': round(best_score, 3),
                'reason': (
                    f"Topic too similar to script #{best_match['id']} "
                    f"(similarity: {best_score:.2f})"
                ),
            }

        return {
            'is_duplicate': False,
            'similar_script_id': None,
            'similarity_score': round(best_score, 3),
            'reason': 'Topic is unique',
        }

    def check_hook_duplicate(self, brand_id: str,
                               hook_text: str) -> dict:
        """
        Checks if a hook is too similar to recent hooks for this brand.

        Parameters:
            brand_id: Brand identifier.
            hook_text: The hook text to check.

        Returns:
            Dict with is_duplicate (bool), similar_script_id (optional),
            similarity_score, and reason.

        Side effects:
            Queries the scripts table.
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(
                days=self.ROLLING_WINDOW_DAYS)
        ).isoformat()

        hook_words = set(hook_text.lower().split())
        if len(hook_words) < 3:
            return {
                'is_duplicate': False,
                'similar_script_id': None,
                'similarity_score': 0.0,
                'reason': 'Hook too short for similarity check',
            }

        recent_hooks = self.db.fetch_all(
            "SELECT id, hook FROM scripts "
            "WHERE brand_id=? AND created_at > ? "
            "ORDER BY created_at DESC LIMIT 100",
            (brand_id, cutoff)
        )

        best_match = None
        best_score = 0.0

        for script in recent_hooks:
            existing_hook = script.get('hook', '')
            existing_words = set(existing_hook.lower().split())

            if not existing_words:
                continue

            intersection = hook_words & existing_words
            union = hook_words | existing_words
            similarity = len(intersection) / len(union) if union else 0

            if similarity > best_score:
                best_score = similarity
                best_match = script

        if best_score >= self.HOOK_SIMILARITY_THRESHOLD and best_match:
            return {
                'is_duplicate': True,
                'similar_script_id': best_match['id'],
                'similarity_score': round(best_score, 3),
                'reason': (
                    f"Hook too similar to script #{best_match['id']} "
                    f"(similarity: {best_score:.2f})"
                ),
            }

        return {
            'is_duplicate': False,
            'similar_script_id': None,
            'similarity_score': round(best_score, 3),
            'reason': 'Hook is unique',
        }

    def check_full_script(self, brand_id: str,
                           topic: str,
                           hook_text: str,
                           script_text: str) -> dict:
        """
        Comprehensive duplicate check on all script components.

        Parameters:
            brand_id: Brand identifier.
            topic: Script topic.
            hook_text: Script hook text.
            script_text: Full script text.

        Returns:
            Dict with is_duplicate (bool), checks (dict of individual
            check results), and overall reason.

        Side effects:
            Queries the database multiple times.
            Also runs cross-brand dedup if available.
        """
        topic_check = self.check_topic_duplicate(brand_id, topic)
        hook_check = self.check_hook_duplicate(brand_id, hook_text)

        # Cross-brand check
        cross_brand_check = {'is_duplicate': False}
        try:
            from modules.compliance.cross_brand_dedup import \
                CrossBrandDeduplicator
            dedup = CrossBrandDeduplicator()
            is_unique = dedup.check_script_uniqueness(
                brand_id, script_text
            )
            cross_brand_check = {
                'is_duplicate': not is_unique,
                'reason': 'Cross-brand similarity detected' if not is_unique
                          else 'Unique across brands',
            }
        except Exception as e:
            logger.warning("cross_brand_check_failed", error=str(e))

        is_duplicate = (
            topic_check['is_duplicate'] or
            hook_check['is_duplicate'] or
            cross_brand_check.get('is_duplicate', False)
        )

        reasons = []
        if topic_check['is_duplicate']:
            reasons.append(f"Topic: {topic_check['reason']}")
        if hook_check['is_duplicate']:
            reasons.append(f"Hook: {hook_check['reason']}")
        if cross_brand_check.get('is_duplicate'):
            reasons.append(f"Cross-brand: {cross_brand_check.get('reason', '')}")

        return {
            'is_duplicate': is_duplicate,
            'checks': {
                'topic': topic_check,
                'hook': hook_check,
                'cross_brand': cross_brand_check,
            },
            'reason': '; '.join(reasons) if reasons else 'All checks passed',
        }
