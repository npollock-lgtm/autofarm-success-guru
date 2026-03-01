"""
Hashtag generator for AutoFarm Zero — Success Guru Network v6.0.

Generates platform-optimized hashtag sets for each piece of content.
Uses brand-specific hashtag pools (core, rotating, trending) with
overlap control to prevent platform detection of coordinated accounts.

Platform hashtag limits:
- TikTok: 5 max recommended (more can reduce reach)
- Instagram: 30 max, 5-10 recommended
- Facebook: 3-5 recommended
- YouTube: 500 char max in tags field
- Snapchat: 3-5 recommended
"""

import json
import random
import logging
from datetime import datetime, timezone
from typing import Optional

import structlog

from database.db import Database
from modules.ai_brain.llm_router import LLMRouter

logger = structlog.get_logger(__name__)


class HashtagGenerator:
    """
    Generates platform-optimized hashtags for content.

    Combines core brand hashtags, rotating niche hashtags, and
    trending topic hashtags. Ensures ≤60% overlap between posts
    for the same brand to avoid spam detection.

    Attributes:
        PLATFORM_LIMITS: Maximum hashtags per platform.
        MAX_OVERLAP_PERCENT: Maximum overlap between consecutive posts.
    """

    PLATFORM_LIMITS: dict = {
        'tiktok': 5,
        'instagram': 10,
        'facebook': 5,
        'youtube': 15,
        'snapchat': 5,
    }

    MAX_OVERLAP_PERCENT: float = 0.6  # 60% maximum overlap

    def __init__(self) -> None:
        """
        Initializes the HashtagGenerator.

        Side effects:
            Creates LLMRouter and Database instances.
            Loads hashtag pools from cached responses.
        """
        self.llm_router = LLMRouter()
        self.db = Database()
        self._hashtag_pools = self._load_hashtag_pools()

    def generate_hashtags(self, brand_id: str, script_text: str,
                          platform: str,
                          brand_config: dict) -> list[str]:
        """
        Generates a set of hashtags for a piece of content.

        Parameters:
            brand_id: Brand identifier.
            script_text: Full script text for topic extraction.
            platform: Target platform for count limits.
            brand_config: Brand configuration.

        Returns:
            List of hashtag strings (with # prefix).

        Side effects:
            May make LLM call for topic-specific hashtags.
            Checks overlap with recent posts for this brand.
        """
        limit = self.PLATFORM_LIMITS.get(platform, 5)
        pool = self._hashtag_pools.get(brand_id, {})

        # Core hashtags (always included, 30-40% of total)
        core = pool.get('core', [])
        core_count = max(1, int(limit * 0.3))
        selected_core = random.sample(core, min(core_count, len(core))) if core else []

        # Rotating niche hashtags (40-50% of total)
        rotating = pool.get('rotating', [])
        rotating_count = max(1, int(limit * 0.4))
        selected_rotating = random.sample(
            rotating, min(rotating_count, len(rotating))
        ) if rotating else []

        # Topic-specific hashtags (remaining slots)
        remaining = limit - len(selected_core) - len(selected_rotating)
        topic_hashtags = self._generate_topic_hashtags(
            brand_id, script_text, platform, remaining
        )

        # Combine all hashtags
        all_hashtags = selected_core + selected_rotating + topic_hashtags

        # Ensure # prefix
        all_hashtags = [
            f"#{tag.lstrip('#')}" for tag in all_hashtags
        ]

        # Check overlap with recent posts
        all_hashtags = self._reduce_overlap(
            brand_id, platform, all_hashtags
        )

        # Apply platform-specific limits
        all_hashtags = all_hashtags[:limit]

        # Apply platform-specific adjustments
        all_hashtags = self._platform_adjust(platform, all_hashtags)

        logger.info("hashtags_generated",
                      brand_id=brand_id,
                      platform=platform,
                      count=len(all_hashtags))

        return all_hashtags

    def _generate_topic_hashtags(self, brand_id: str,
                                   script_text: str,
                                   platform: str,
                                   count: int) -> list[str]:
        """
        Generates topic-specific hashtags from script content.

        Parameters:
            brand_id: Brand identifier.
            script_text: Script text for topic extraction.
            platform: Target platform.
            count: Number of topic hashtags needed.

        Returns:
            List of hashtag strings.

        Side effects:
            Makes LLM call for topic extraction.
            Falls back to keyword extraction on failure.
        """
        if count <= 0:
            return []

        try:
            prompt = (
                f"Generate exactly {count} relevant hashtags for this "
                f"short-form video script on {platform}.\n\n"
                f"Script excerpt: {script_text[:300]}\n\n"
                f"Requirements:\n"
                f"- Each hashtag should be 1-3 words, lowercase, no spaces\n"
                f"- Mix popular and niche hashtags\n"
                f"- No generic hashtags like #foryou or #viral\n"
                f"- Relevant to the specific content topic\n\n"
                f"Respond with ONLY the hashtags, one per line, with # prefix."
            )

            result = self.llm_router.generate(
                prompt=prompt,
                task_type='hashtag_generation',
                max_tokens=100,
                temperature=0.7,
            )

            text = result.get('text', '')
            hashtags = [
                line.strip() for line in text.split('\n')
                if line.strip().startswith('#')
            ]

            return hashtags[:count]

        except Exception as e:
            logger.warning("topic_hashtag_generation_failed",
                            brand_id=brand_id, error=str(e))
            return self._fallback_topic_hashtags(script_text, count)

    def _fallback_topic_hashtags(self, script_text: str,
                                   count: int) -> list[str]:
        """
        Extracts hashtags from script keywords as a fallback.

        Parameters:
            script_text: Script text to extract keywords from.
            count: Number of hashtags needed.

        Returns:
            List of hashtag strings derived from script keywords.
        """
        # Simple keyword extraction
        stop_words = {
            'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be',
            'been', 'being', 'have', 'has', 'had', 'do', 'does',
            'did', 'will', 'shall', 'would', 'could', 'should',
            'may', 'might', 'must', 'can', 'this', 'that', 'these',
            'those', 'it', 'its', 'you', 'your', 'they', 'them',
            'their', 'we', 'our', 'but', 'and', 'or', 'not', 'no',
            'for', 'to', 'of', 'in', 'on', 'at', 'by', 'with',
            'from', 'as', 'into', 'about', 'than', 'so', 'if',
            'when', 'what', 'how', 'why', 'who', 'where', 'which',
            'all', 'each', 'every', 'both', 'few', 'more', 'most',
            'some', 'any', 'just', 'only', 'very', 'too', 'also',
        }

        words = script_text.lower().split()
        keywords = [
            w.strip('.,!?;:()[]"\'')
            for w in words
            if w.strip('.,!?;:()[]"\'') not in stop_words
            and len(w) > 3
        ]

        # Count frequency
        freq = {}
        for w in keywords:
            freq[w] = freq.get(w, 0) + 1

        # Sort by frequency
        sorted_words = sorted(freq.items(), key=lambda x: x[1], reverse=True)
        top_words = [w for w, _ in sorted_words[:count]]

        return [f"#{w}" for w in top_words]

    def _reduce_overlap(self, brand_id: str, platform: str,
                          hashtags: list[str]) -> list[str]:
        """
        Reduces overlap with recent posts for this brand+platform.

        Parameters:
            brand_id: Brand identifier.
            platform: Platform name.
            hashtags: Proposed hashtag list.

        Returns:
            Modified hashtag list with reduced overlap.

        Side effects:
            Queries publish_jobs for recent hashtag data.
        """
        # Get recent hashtags
        try:
            recent = self.db.fetch_all(
                "SELECT hashtags FROM publish_jobs "
                "WHERE brand_id=? AND platform=? AND status='published' "
                "ORDER BY published_at DESC LIMIT 5",
                (brand_id, platform)
            )

            if not recent:
                return hashtags

            # Collect all recent hashtags
            recent_tags = set()
            for row in recent:
                tags_str = row.get('hashtags', '') or ''
                for tag in tags_str.split(','):
                    tag = tag.strip()
                    if tag:
                        recent_tags.add(tag.lower())

            if not recent_tags:
                return hashtags

            # Count overlap
            current_tags = {t.lower() for t in hashtags}
            overlap = current_tags & recent_tags
            overlap_ratio = len(overlap) / len(current_tags) if current_tags else 0

            if overlap_ratio > self.MAX_OVERLAP_PERCENT:
                # Remove overlapping tags and add from rotating pool
                pool = self._hashtag_pools.get(brand_id, {})
                rotating = pool.get('rotating', [])
                non_overlapping = [
                    t for t in hashtags if t.lower() not in recent_tags
                ]

                # Fill remaining slots from rotating pool
                available_rotating = [
                    f"#{t.lstrip('#')}" for t in rotating
                    if f"#{t.lstrip('#')}".lower() not in recent_tags
                ]
                random.shuffle(available_rotating)

                needed = len(hashtags) - len(non_overlapping)
                hashtags = non_overlapping + available_rotating[:needed]

                logger.debug("hashtag_overlap_reduced",
                              brand_id=brand_id,
                              platform=platform,
                              original_overlap=round(overlap_ratio, 2))

        except Exception as e:
            logger.warning("hashtag_overlap_check_failed", error=str(e))

        return hashtags

    def _platform_adjust(self, platform: str,
                           hashtags: list[str]) -> list[str]:
        """
        Applies platform-specific hashtag adjustments.

        Parameters:
            platform: Target platform.
            hashtags: List of hashtags.

        Returns:
            Adjusted hashtag list.
        """
        if platform == 'youtube':
            # YouTube uses tags, not hashtags in description
            # Remove # prefix for YouTube tags field
            return [t.lstrip('#') for t in hashtags]

        return hashtags

    def _load_hashtag_pools(self) -> dict:
        """
        Loads per-brand hashtag pools from cached responses.

        Returns:
            Dict mapping brand_id to hashtag pool config.

        Side effects:
            Reads config/cached_responses/hashtag_generation.json.
        """
        try:
            import os
            config_path = os.path.join(
                os.getenv('APP_DIR', '/app'),
                'config', 'cached_responses', 'hashtag_generation.json'
            )

            with open(config_path, 'r') as f:
                data = json.load(f)

            return data.get('brands', data)

        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.warning("hashtag_pools_load_failed", error=str(e))
            return {}
