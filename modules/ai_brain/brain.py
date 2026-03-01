"""
AI Brain orchestrator for AutoFarm Zero — Success Guru Network v6.0.

Central orchestration module for all AI-powered content generation.
Coordinates the full pipeline from trend → script → classification,
with cross-brand deduplication, quality checks, and state management.

Pipeline flow:
1. Select best unused trend for brand
2. Generate hook (HookEngine)
3. Generate script body (ScriptWriter)
4. Classify content (ContentClassifier)
5. Generate hashtags (HashtagGenerator)
6. Check duplicates (DuplicateChecker)
7. Pass to quality gate
"""

import logging
import time
from datetime import datetime, timezone
from typing import Optional

import structlog

from database.db import Database
from modules.ai_brain.llm_router import LLMRouter
from modules.ai_brain.hook_engine import HookEngine
from modules.ai_brain.script_writer import ScriptWriter
from modules.ai_brain.classifier import ContentClassifier
from modules.ai_brain.duplicate_checker import DuplicateChecker
from modules.ai_brain.hashtag_generator import HashtagGenerator
from modules.ai_brain.brand_generator import BrandConfigGenerator
from modules.trend_scanner.scanner import TrendScanner
from modules.infrastructure.job_state_machine import JobStateMachine, JobState
from modules.infrastructure.resource_scheduler import get_scheduler

logger = structlog.get_logger(__name__)


class AIBrain:
    """
    Central orchestration for all AI-powered content generation.

    Coordinates the full content pipeline from trend discovery through
    script generation, classification, and hashtag creation. Manages
    the job state machine to track content through the pipeline.

    Attributes:
        MAX_SCRIPTS_PER_BRAND_PER_RUN: Maximum scripts to generate per
            brand in a single run.
        CONTENT_DIVERSITY_WINDOW: Number of recent scripts to check for
            pillar diversity.
    """

    MAX_SCRIPTS_PER_BRAND_PER_RUN: int = 3
    CONTENT_DIVERSITY_WINDOW: int = 10

    def __init__(self) -> None:
        """
        Initializes the AIBrain with all sub-components.

        Side effects:
            Creates instances of all AI module components.
        """
        self.db = Database()
        self.llm_router = LLMRouter()
        self.hook_engine = HookEngine()
        self.script_writer = ScriptWriter()
        self.classifier = ContentClassifier()
        self.duplicate_checker = DuplicateChecker()
        self.hashtag_generator = HashtagGenerator()
        self.trend_scanner = TrendScanner()
        self.state_machine = JobStateMachine()
        self._scheduler = get_scheduler()

    def generate_content_for_brand(self, brand_id: str,
                                     brand_config: dict,
                                     max_scripts: Optional[int] = None
                                     ) -> dict:
        """
        Generates content scripts for a single brand.

        Parameters:
            brand_id: Brand identifier.
            brand_config: Full brand configuration from brands.json.
            max_scripts: Override for maximum scripts to generate.

        Returns:
            Dict with generated_count, failed_count, scripts (list),
            and duration_seconds.

        Side effects:
            Generates scripts via LLM.
            Creates job state records.
            Stores scripts in the database.
        """
        max_scripts = max_scripts or self.MAX_SCRIPTS_PER_BRAND_PER_RUN
        start_time = time.time()

        result = {
            'brand_id': brand_id,
            'generated_count': 0,
            'failed_count': 0,
            'scripts': [],
            'duration_seconds': 0,
        }

        # Check resource availability
        can_start, reason = self._scheduler.can_start_job('llm_inference')
        if not can_start:
            logger.warning("content_generation_skipped_resources",
                            brand_id=brand_id, reason=reason)
            result['skipped_reason'] = reason
            return result

        # Get available trends
        trends = self.trend_scanner.get_available_trends(
            brand_id, limit=max_scripts * 2
        )

        if not trends:
            logger.info("no_trends_available",
                          brand_id=brand_id)
            result['skipped_reason'] = 'No trends available'
            return result

        # Get recent pillars for diversity
        recent_pillars = self._get_recent_pillars(brand_id)

        for trend in trends:
            if result['generated_count'] >= max_scripts:
                break

            # Check shutdown
            try:
                from modules.infrastructure.shutdown_handler import \
                    is_shutting_down
                if is_shutting_down():
                    break
            except ImportError:
                pass

            try:
                script = self._generate_single_script(
                    brand_id, trend, brand_config, recent_pillars
                )

                if script:
                    result['generated_count'] += 1
                    result['scripts'].append(script)

                    # Mark trend as used
                    self.trend_scanner.consume_trend(trend['id'])

                    # Update recent pillars
                    if script.get('pillar'):
                        recent_pillars.append(script['pillar'])
                else:
                    result['failed_count'] += 1

            except Exception as e:
                result['failed_count'] += 1
                logger.error("script_generation_error",
                              brand_id=brand_id,
                              trend_id=trend['id'],
                              error=str(e))

        result['duration_seconds'] = round(
            time.time() - start_time, 2
        )

        logger.info("brand_content_generation_complete",
                      brand_id=brand_id,
                      generated=result['generated_count'],
                      failed=result['failed_count'],
                      duration_s=result['duration_seconds'])

        return result

    def generate_content_all_brands(self) -> dict:
        """
        Generates content for all active brands.

        Returns:
            Dict with per-brand results and total counts.

        Side effects:
            Generates scripts for all brands via LLM.
            May scan for new trends if brands need them.
        """
        from config.settings import load_brands_config
        brands = load_brands_config()

        results = {
            'total_generated': 0,
            'total_failed': 0,
            'per_brand': {},
            'duration_seconds': 0,
        }
        start = time.time()

        for brand_id, brand_config in brands.items():
            # Check shutdown
            try:
                from modules.infrastructure.shutdown_handler import \
                    is_shutting_down
                if is_shutting_down():
                    break
            except ImportError:
                pass

            # Check if brand needs trends
            if self.trend_scanner.brand_needs_trends(brand_id):
                logger.info("scanning_trends_for_brand",
                              brand_id=brand_id)
                self.trend_scanner.scan_brand(brand_id, brand_config)

            # Generate content
            brand_result = self.generate_content_for_brand(
                brand_id, brand_config
            )
            results['per_brand'][brand_id] = brand_result
            results['total_generated'] += brand_result['generated_count']
            results['total_failed'] += brand_result['failed_count']

        results['duration_seconds'] = round(time.time() - start, 2)

        logger.info("all_brands_generation_complete",
                      total_generated=results['total_generated'],
                      total_failed=results['total_failed'],
                      duration_s=results['duration_seconds'])

        return results

    def _generate_single_script(self, brand_id: str,
                                  trend: dict,
                                  brand_config: dict,
                                  recent_pillars: list[str]
                                  ) -> Optional[dict]:
        """
        Generates a single script from a trend.

        Parameters:
            brand_id: Brand identifier.
            trend: Trend dict from the database.
            brand_config: Brand configuration.
            recent_pillars: List of recently used pillars for diversity.

        Returns:
            Script dict if generation succeeds, None otherwise.

        Side effects:
            Creates job state record.
            Makes LLM calls.
            Stores script in database.
        """
        topic = trend.get('topic', '')
        trend_id = trend.get('id')

        # Create job state
        job_id = self.state_machine.create_job(
            brand_id=brand_id,
            job_type='content_generation'
        )

        try:
            # Transition to TREND_FOUND
            self.state_machine.transition(
                job_id, JobState.TREND_FOUND
            )

            # Check for duplicate topic
            dup_check = self.duplicate_checker.check_topic_duplicate(
                brand_id, topic
            )
            if dup_check['is_duplicate']:
                logger.info("trend_topic_duplicate",
                              brand_id=brand_id,
                              topic=topic,
                              reason=dup_check['reason'])
                return None

            # Generate script
            self.state_machine.transition(
                job_id, JobState.SCRIPT_DRAFT
            )

            script = self.script_writer.generate_script(
                brand_id=brand_id,
                topic=topic,
                brand_config=brand_config,
                trend_id=trend_id,
            )

            if not script:
                self.state_machine.transition(
                    job_id, JobState.FAILED,
                    error_message="Script generation failed"
                )
                return None

            # Classify content
            classification = self.classifier.classify_script(
                script['script_text'], brand_id, brand_config
            )
            script['classification'] = classification
            script['pillar'] = classification.get('pillar', '')

            # Check content diversity
            if not self._check_diversity(
                script.get('pillar', ''), recent_pillars,
                brand_config
            ):
                logger.info("script_lacks_diversity",
                              brand_id=brand_id,
                              pillar=script.get('pillar'))
                # Don't fail — just note it

            # Generate hashtags for each platform
            platforms = brand_config.get('platforms', ['tiktok'])
            script['hashtags'] = {}
            for platform in platforms:
                script['hashtags'][platform] = \
                    self.hashtag_generator.generate_hashtags(
                        brand_id, script['script_text'],
                        platform, brand_config
                    )

            # Update job state
            self.state_machine.transition(
                job_id, JobState.SCRIPT_APPROVED
            )

            script['job_id'] = job_id

            return script

        except Exception as e:
            try:
                self.state_machine.transition(
                    job_id, JobState.FAILED,
                    error_message=str(e)
                )
            except Exception:
                pass
            raise

    def _get_recent_pillars(self, brand_id: str) -> list[str]:
        """
        Gets recently used content pillars for diversity checking.

        Parameters:
            brand_id: Brand identifier.

        Returns:
            List of pillar strings from recent scripts.
        """
        rows = self.db.fetch_all(
            "SELECT pillar FROM scripts WHERE brand_id=? "
            "AND pillar IS NOT NULL AND pillar != '' "
            "ORDER BY created_at DESC LIMIT ?",
            (brand_id, self.CONTENT_DIVERSITY_WINDOW)
        )
        return [row['pillar'] for row in rows]

    def _check_diversity(self, pillar: str,
                          recent_pillars: list[str],
                          brand_config: dict) -> bool:
        """
        Checks if adding this pillar maintains content diversity.

        Parameters:
            pillar: The pillar of the new script.
            recent_pillars: List of recently used pillars.
            brand_config: Brand configuration with all pillars.

        Returns:
            True if diversity is maintained, False if too concentrated.
        """
        if not pillar or not recent_pillars:
            return True

        all_pillars = brand_config.get('pillars', [])
        if len(all_pillars) <= 1:
            return True

        # Count how many of the last N scripts use this pillar
        same_count = sum(1 for p in recent_pillars if p == pillar)
        max_concentration = len(recent_pillars) / len(all_pillars) + 1

        return same_count < max_concentration

    def get_llm_status(self) -> dict:
        """
        Returns LLM router health and usage status.

        Returns:
            Dict with provider health, usage stats, and routing info.
        """
        return self.llm_router.get_status()

    def get_generation_stats(self) -> dict:
        """
        Returns content generation statistics.

        Returns:
            Dict with per-brand generation counts, average quality,
            and pipeline health metrics.
        """
        # Scripts generated today
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        rows = self.db.fetch_all(
            "SELECT brand_id, COUNT(*) as count, "
            "AVG(word_count) as avg_words, "
            "AVG(safety_score) as avg_safety "
            "FROM scripts WHERE DATE(created_at)=? "
            "GROUP BY brand_id",
            (today,)
        )

        per_brand = {
            row['brand_id']: {
                'scripts_today': row['count'],
                'avg_word_count': round(row['avg_words'] or 0, 1),
                'avg_safety_score': round(row['avg_safety'] or 0, 3),
            }
            for row in rows
        }

        # Total scripts
        total = self.db.fetch_one(
            "SELECT COUNT(*) as total FROM scripts"
        )

        return {
            'per_brand_today': per_brand,
            'total_scripts': total['total'] if total else 0,
            'llm_status': self.get_llm_status(),
        }
