"""
Hook engine for AutoFarm Zero — Success Guru Network v6.0.

Generates attention-grabbing hooks for short-form video content.
Uses brand-specific hook types and performance-weighted selection.
Hooks are the first 3 seconds of a video — they determine whether
a viewer stays or scrolls.

Hook selection is performance-weighted: hook types that historically
achieve higher 3-second hold rates are selected more frequently.
New hook types start with equal weight and adapt over time.
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


class HookEngine:
    """
    Generates and manages hooks for video content.

    Selects hook types using performance-weighted random selection,
    then generates specific hook text using the LLM router.
    Tracks hook performance over time to optimize selection.

    Attributes:
        DEFAULT_WEIGHT: Starting weight for new hook types.
        MIN_WEIGHT: Minimum weight to prevent zero-probability.
        HOOK_TEMPLATES: Fallback templates per hook type category.
    """

    DEFAULT_WEIGHT: float = 1.0
    MIN_WEIGHT: float = 0.1

    HOOK_TEMPLATES: dict = {
        'contrarian_truth': [
            "Everything you've been told about {topic} is wrong.",
            "The uncomfortable truth about {topic} nobody wants to admit.",
            "{topic}? Here's what they're not telling you.",
        ],
        'dark_psychology': [
            "The psychological trick behind {topic}.",
            "Why your brain is lying to you about {topic}.",
            "The hidden pattern in {topic} that controls your life.",
        ],
        'forbidden_knowledge': [
            "This secret about {topic} was hidden for years.",
            "The one thing about {topic} they don't want you to know.",
            "I discovered something about {topic} that changes everything.",
        ],
        'stoic_wisdom': [
            "Marcus Aurelius understood {topic} 2000 years ago.",
            "The ancient Stoic perspective on {topic}.",
            "What the Stoics knew about {topic} that we've forgotten.",
        ],
        'wealth_system': [
            "The system behind {topic} that actually builds wealth.",
            "How {topic} creates millionaires in silence.",
            "The {topic} strategy nobody talks about.",
        ],
        'financial_truth': [
            "The financial truth about {topic} that will change your mind.",
            "Why most people get {topic} completely wrong.",
            "Here's the math behind {topic} that matters.",
        ],
        'stoic_reflection': [
            "In the silence of {topic}, find your truth.",
            "The stillness within {topic} holds the answer.",
            "Consider {topic} — not as the world sees it, but as it is.",
        ],
        'ancient_wisdom': [
            "The ancient masters taught this about {topic}.",
            "2000 years of wisdom on {topic} in 60 seconds.",
            "What {topic} meant before the modern world corrupted it.",
        ],
        'social_insight': [
            "The social pattern in {topic} that 99% miss.",
            "Why {topic} reveals everything about someone.",
            "The hidden language of {topic}.",
        ],
        'habit_science': [
            "The neuroscience behind {topic} is fascinating.",
            "Your brain on {topic}: what actually happens.",
            "The compound effect of {topic} over 365 days.",
        ],
        'attachment_insight': [
            "Your attachment style explains {topic} perfectly.",
            "The psychology of {topic} in relationships.",
            "Why {topic} triggers you — and what it means.",
        ],
        'emotional_truth': [
            "The emotional truth about {topic} nobody prepared you for.",
            "When {topic} happens, your heart knows before your mind.",
            "This is what {topic} actually feels like.",
        ],
    }

    def __init__(self) -> None:
        """
        Initializes the HookEngine with LLM router and database.

        Side effects:
            Creates LLMRouter and Database instances.
        """
        self.llm_router = LLMRouter()
        self.db = Database()

    def generate_hook(self, brand_id: str, topic: str,
                      brand_config: dict,
                      platform: str = 'tiktok') -> dict:
        """
        Generates a hook for a given topic and brand.

        Parameters:
            brand_id: Brand identifier.
            topic: The topic/trend to create a hook for.
            brand_config: Brand configuration from brands.json.
            platform: Target platform for platform-specific optimization.

        Returns:
            Dict with keys: hook_text, hook_type, confidence_score.

        Side effects:
            Makes LLM call via LLMRouter for hook generation.
            Falls back to templates if LLM is unavailable.
        """
        # Select hook type based on performance weights
        hook_type = self._select_hook_type(brand_id, brand_config, platform)

        # Generate hook text
        hook_text = self._generate_hook_text(
            brand_id, hook_type, topic, brand_config
        )

        # Validate hook
        if not hook_text or len(hook_text) < 10:
            # Fallback to template
            hook_text = self._template_hook(hook_type, topic)

        return {
            'hook_text': hook_text,
            'hook_type': hook_type,
            'confidence_score': self._score_hook(hook_text, brand_config),
        }

    def _select_hook_type(self, brand_id: str,
                           brand_config: dict,
                           platform: str) -> str:
        """
        Selects a hook type using performance-weighted random selection.

        Parameters:
            brand_id: Brand identifier.
            brand_config: Brand configuration with hook_priority list.
            platform: Target platform for performance lookup.

        Returns:
            Selected hook type string.

        Side effects:
            Reads hook_performance table for weights.
        """
        hook_types = brand_config.get('hook_priority', [])
        if not hook_types:
            hook_types = list(self.HOOK_TEMPLATES.keys())

        # Get performance weights from database
        weights = {}
        for hook_type in hook_types:
            row = self.db.fetch_one(
                "SELECT weight FROM hook_performance "
                "WHERE brand_id=? AND hook_type=? AND platform=?",
                (brand_id, hook_type, platform)
            )
            weights[hook_type] = row['weight'] if row else self.DEFAULT_WEIGHT

        # Ensure minimum weight
        weights = {
            k: max(v, self.MIN_WEIGHT) for k, v in weights.items()
        }

        # Weighted random selection
        total_weight = sum(weights.values())
        r = random.uniform(0, total_weight)
        cumulative = 0.0

        for hook_type, weight in weights.items():
            cumulative += weight
            if r <= cumulative:
                return hook_type

        return hook_types[0]  # Fallback

    def _generate_hook_text(self, brand_id: str, hook_type: str,
                             topic: str, brand_config: dict) -> str:
        """
        Generates hook text using the LLM router.

        Parameters:
            brand_id: Brand identifier.
            hook_type: Selected hook type.
            topic: Topic for the hook.
            brand_config: Brand configuration for voice/style.

        Returns:
            Generated hook text string.

        Side effects:
            Makes LLM call via LLMRouter.
        """
        voice = brand_config.get('voice_persona', {})
        positioning = brand_config.get('positioning', '')
        forbidden = voice.get('forbidden_words', [])
        signature_phrases = voice.get('signature_phrases', [])

        prompt = (
            f"Generate a compelling video hook for a {hook_type} style video.\n"
            f"Topic: {topic}\n"
            f"Brand positioning: {positioning}\n"
            f"Voice style: {voice.get('tone', 'authoritative')}, "
            f"{voice.get('pacing', 'measured')}\n"
            f"Forbidden words: {', '.join(forbidden)}\n"
            f"Example signature phrases: {', '.join(signature_phrases[:2])}\n\n"
            f"Requirements:\n"
            f"- Must grab attention in the first 3 seconds\n"
            f"- Must be under 20 words\n"
            f"- Must create curiosity or challenge assumptions\n"
            f"- Must match the brand's voice and tone\n"
            f"- Do NOT use forbidden words\n\n"
            f"Respond with ONLY the hook text. No quotes, no explanation."
        )

        try:
            result = self.llm_router.generate(
                prompt=prompt,
                task_type='hook_generation',
                max_tokens=100,
                temperature=0.8,
            )

            hook_text = result.get('text', '').strip()
            # Clean up any quotes or formatting
            hook_text = hook_text.strip('"\'')

            # Validate against forbidden words
            for word in forbidden:
                if word.lower() in hook_text.lower():
                    logger.warning("hook_contains_forbidden_word",
                                    brand_id=brand_id,
                                    word=word)
                    return self._template_hook(hook_type, topic)

            return hook_text

        except Exception as e:
            logger.warning("hook_generation_failed",
                            brand_id=brand_id, error=str(e))
            return self._template_hook(hook_type, topic)

    def _template_hook(self, hook_type: str, topic: str) -> str:
        """
        Generates a hook from templates as a fallback.

        Parameters:
            hook_type: Hook type to select template category.
            topic: Topic to insert into template.

        Returns:
            Hook text generated from template.
        """
        # Find the best matching template category
        templates = self.HOOK_TEMPLATES.get(hook_type)
        if not templates:
            # Try partial match
            for key, temps in self.HOOK_TEMPLATES.items():
                if key in hook_type or hook_type in key:
                    templates = temps
                    break

        if not templates:
            templates = list(self.HOOK_TEMPLATES.values())[0]

        template = random.choice(templates)
        # Truncate topic for the template if too long
        short_topic = topic[:50] if len(topic) > 50 else topic
        return template.format(topic=short_topic)

    def _score_hook(self, hook_text: str,
                     brand_config: dict) -> float:
        """
        Scores a hook's predicted effectiveness.

        Parameters:
            hook_text: The generated hook text.
            brand_config: Brand configuration for voice matching.

        Returns:
            Confidence score between 0.0 and 1.0.
        """
        score = 0.5  # Base score

        # Length check (ideal: 5-15 words)
        word_count = len(hook_text.split())
        if 5 <= word_count <= 15:
            score += 0.1
        elif word_count > 20:
            score -= 0.1

        # Question mark bonus (curiosity)
        if '?' in hook_text:
            score += 0.1

        # Forbidden word check
        voice = brand_config.get('voice_persona', {})
        forbidden = voice.get('forbidden_words', [])
        for word in forbidden:
            if word.lower() in hook_text.lower():
                score -= 0.3

        # Power words check
        power_words = [
            'secret', 'truth', 'hidden', 'powerful', 'ancient',
            'nobody', 'everything', 'never', 'always', 'wrong',
            'mistake', 'discover', 'changed', 'dangerous',
        ]
        for word in power_words:
            if word in hook_text.lower():
                score += 0.05

        return max(min(score, 1.0), 0.0)

    def update_hook_performance(self, brand_id: str, hook_type: str,
                                 platform: str,
                                 three_second_hold: float,
                                 retention_rate: float,
                                 cps_score: float) -> None:
        """
        Updates hook performance metrics from analytics data.

        Parameters:
            brand_id: Brand identifier.
            hook_type: Hook type used.
            platform: Platform the content was published on.
            three_second_hold: Percentage of viewers who stayed 3+ seconds.
            retention_rate: Average retention rate.
            cps_score: Combined performance score.

        Side effects:
            Updates hook_performance table with running averages.
            Adjusts weight based on performance relative to brand average.
        """
        existing = self.db.fetch_one(
            "SELECT * FROM hook_performance "
            "WHERE brand_id=? AND hook_type=? AND platform=?",
            (brand_id, hook_type, platform)
        )

        now = datetime.now(timezone.utc).isoformat()

        if existing:
            # Update running averages
            n = existing['sample_count']
            new_hold = (existing['avg_three_second_hold'] * n + three_second_hold) / (n + 1)
            new_retention = (existing['avg_retention_rate'] * n + retention_rate) / (n + 1)
            new_cps = (existing['avg_cps_score'] * n + cps_score) / (n + 1)

            # Calculate new weight based on performance vs average
            brand_avg = self._get_brand_average_cps(brand_id, platform)
            weight = max(self.MIN_WEIGHT, new_cps / brand_avg if brand_avg > 0 else 1.0)

            self.db.execute_write(
                "UPDATE hook_performance SET "
                "avg_three_second_hold=?, avg_retention_rate=?, "
                "avg_cps_score=?, sample_count=?, weight=?, "
                "last_updated=? "
                "WHERE brand_id=? AND hook_type=? AND platform=?",
                (new_hold, new_retention, new_cps, n + 1, weight, now,
                 brand_id, hook_type, platform)
            )
        else:
            self.db.execute_write(
                "INSERT INTO hook_performance "
                "(brand_id, hook_type, platform, avg_three_second_hold, "
                "avg_retention_rate, avg_cps_score, sample_count, weight, "
                "last_updated) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (brand_id, hook_type, platform, three_second_hold,
                 retention_rate, cps_score, self.DEFAULT_WEIGHT, now)
            )

        logger.info("hook_performance_updated",
                      brand_id=brand_id, hook_type=hook_type,
                      platform=platform,
                      three_second_hold=three_second_hold)

    def _get_brand_average_cps(self, brand_id: str,
                                platform: str) -> float:
        """
        Gets the average CPS score across all hook types for a brand.

        Parameters:
            brand_id: Brand identifier.
            platform: Platform name.

        Returns:
            Average CPS score, or 1.0 if no data.
        """
        row = self.db.fetch_one(
            "SELECT AVG(avg_cps_score) as avg_cps "
            "FROM hook_performance "
            "WHERE brand_id=? AND platform=? AND sample_count > 0",
            (brand_id, platform)
        )
        return row['avg_cps'] if row and row['avg_cps'] else 1.0

    def get_hook_stats(self, brand_id: str) -> list[dict]:
        """
        Returns hook performance statistics for a brand.

        Parameters:
            brand_id: Brand identifier.

        Returns:
            List of dicts with hook type performance data.
        """
        rows = self.db.fetch_all(
            "SELECT * FROM hook_performance WHERE brand_id=? "
            "ORDER BY weight DESC",
            (brand_id,)
        )
        return [dict(row) for row in rows]
