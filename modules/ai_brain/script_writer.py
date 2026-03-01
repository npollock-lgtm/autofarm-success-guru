"""
Script writer for AutoFarm Zero — Success Guru Network v6.0.

Generates complete video scripts using the LLM router. Scripts follow
a strict structure: hook → body → CTA, with brand-specific voice,
tone, and forbidden word enforcement.

All script generation uses LLMRouter (primarily Ollama for routine
generation, Groq for complex/rare tasks). Cross-brand deduplication
is performed after generation.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

import structlog

from database.db import Database
from modules.ai_brain.llm_router import LLMRouter
from modules.ai_brain.hook_engine import HookEngine
from modules.compliance.cross_brand_dedup import CrossBrandDeduplicator

logger = structlog.get_logger(__name__)


class ScriptWriter:
    """
    Generates complete video scripts for short-form content.

    Scripts follow the structure: Hook → Body → CTA.
    Each script is tailored to the brand's voice, niche, and pillars.
    Cross-brand deduplication ensures content uniqueness across
    the network.

    Attributes:
        TARGET_WORD_COUNT: Target words for a 30-60s video.
        MAX_WORD_COUNT: Absolute maximum script length.
        MIN_WORD_COUNT: Minimum viable script length.
        MAX_GENERATION_ATTEMPTS: Max retries for dedup/quality failures.
    """

    TARGET_WORD_COUNT: int = 120
    MAX_WORD_COUNT: int = 180
    MIN_WORD_COUNT: int = 60
    MAX_GENERATION_ATTEMPTS: int = 3

    def __init__(self) -> None:
        """
        Initializes the ScriptWriter.

        Side effects:
            Creates LLMRouter, HookEngine, Database, and
            CrossBrandDeduplicator instances.
        """
        self.llm_router = LLMRouter()
        self.hook_engine = HookEngine()
        self.db = Database()
        self.dedup = CrossBrandDeduplicator()

    def generate_script(self, brand_id: str, topic: str,
                        brand_config: dict,
                        trend_id: Optional[int] = None,
                        platform: str = 'tiktok') -> Optional[dict]:
        """
        Generates a complete video script for a brand and topic.

        Parameters:
            brand_id: Brand identifier.
            topic: The topic/trend to write about.
            brand_config: Full brand configuration from brands.json.
            trend_id: Optional trend ID this script is based on.
            platform: Target platform for length/style optimization.

        Returns:
            Dict with keys: hook, hook_type, body, cta, script_text,
            word_count, pillar, llm_provider, llm_tokens_used, trend_id.
            Returns None if generation fails after all attempts.

        Side effects:
            Makes LLM calls via LLMRouter.
            Checks cross-brand deduplication.
            Stores the script in the database.
        """
        for attempt in range(self.MAX_GENERATION_ATTEMPTS):
            try:
                # Generate hook
                hook_result = self.hook_engine.generate_hook(
                    brand_id, topic, brand_config, platform
                )

                # Generate body
                body_result = self._generate_body(
                    brand_id, topic, hook_result['hook_text'],
                    brand_config, platform
                )

                if not body_result:
                    logger.warning("body_generation_failed",
                                    brand_id=brand_id,
                                    attempt=attempt + 1)
                    continue

                # Generate CTA
                cta = self._select_cta(brand_config)

                # Assemble full script
                script_text = (
                    f"{hook_result['hook_text']}\n\n"
                    f"{body_result['body']}\n\n"
                    f"{cta}"
                )
                word_count = len(script_text.split())

                # Check cross-brand deduplication
                is_unique = self.dedup.check_script_uniqueness(
                    brand_id, script_text
                )
                if not is_unique:
                    logger.warning("script_too_similar_to_other_brand",
                                    brand_id=brand_id,
                                    attempt=attempt + 1)
                    continue

                # Determine pillar
                pillar = self._classify_pillar(
                    topic, script_text, brand_config
                )

                # Store in database
                script_data = {
                    'brand_id': brand_id,
                    'trend_id': trend_id,
                    'hook': hook_result['hook_text'],
                    'hook_type': hook_result['hook_type'],
                    'body': body_result['body'],
                    'cta': cta,
                    'script_text': script_text,
                    'word_count': word_count,
                    'pillar': pillar,
                    'llm_provider': body_result.get('provider', 'unknown'),
                    'llm_tokens_used': body_result.get('tokens_used', 0),
                }

                script_id = self._store_script(script_data)
                script_data['id'] = script_id

                logger.info("script_generated",
                              brand_id=brand_id,
                              script_id=script_id,
                              word_count=word_count,
                              hook_type=hook_result['hook_type'],
                              provider=body_result.get('provider'))

                return script_data

            except Exception as e:
                logger.error("script_generation_error",
                              brand_id=brand_id,
                              attempt=attempt + 1,
                              error=str(e))

        logger.error("script_generation_exhausted",
                      brand_id=brand_id,
                      attempts=self.MAX_GENERATION_ATTEMPTS)
        return None

    def _generate_body(self, brand_id: str, topic: str,
                        hook_text: str, brand_config: dict,
                        platform: str) -> Optional[dict]:
        """
        Generates the body portion of the script.

        Parameters:
            brand_id: Brand identifier.
            topic: Script topic.
            hook_text: The generated hook (for context continuity).
            brand_config: Brand configuration.
            platform: Target platform.

        Returns:
            Dict with body text, provider, tokens_used, or None on failure.

        Side effects:
            Makes LLM call via LLMRouter.
        """
        voice = brand_config.get('voice_persona', {})
        positioning = brand_config.get('positioning', '')
        pillars = brand_config.get('pillars', [])
        forbidden = voice.get('forbidden_words', [])
        signature_phrases = voice.get('signature_phrases', [])

        prompt = (
            f"Write the body of a short-form video script.\n\n"
            f"Hook (already written): {hook_text}\n"
            f"Topic: {topic}\n"
            f"Brand positioning: {positioning}\n"
            f"Brand pillars: {', '.join(pillars)}\n"
            f"Voice tone: {voice.get('tone', 'authoritative')}\n"
            f"Voice pacing: {voice.get('pacing', 'measured')}\n"
            f"Vocabulary level: {voice.get('vocabulary_level', 'sophisticated')}\n"
            f"Forbidden words: {', '.join(forbidden)}\n"
            f"Signature style examples: {', '.join(signature_phrases[:2])}\n\n"
            f"Requirements:\n"
            f"- Write ONLY the body text (not hook, not CTA)\n"
            f"- Target length: {self.TARGET_WORD_COUNT - 30} words "
            f"(for a 30-60 second video when combined with hook and CTA)\n"
            f"- Break into 3-4 short paragraphs for natural pausing\n"
            f"- Use the brand's voice and vocabulary level\n"
            f"- Include one concrete example, stat, or study reference\n"
            f"- Build on the hook's promise — deliver value\n"
            f"- Do NOT use any forbidden words\n"
            f"- Do NOT include a CTA — that comes separately\n"
            f"- Platform: {platform} (short-form vertical video)\n\n"
            f"Respond with ONLY the body text. No labels or formatting."
        )

        try:
            result = self.llm_router.generate(
                prompt=prompt,
                task_type='script_generation',
                max_tokens=500,
                temperature=0.7,
            )

            body = result.get('text', '').strip()

            # Validate body
            if not body or len(body.split()) < 20:
                return None

            # Check forbidden words
            for word in forbidden:
                if word.lower() in body.lower():
                    body = body.replace(word, '***')
                    logger.warning("forbidden_word_in_body",
                                    brand_id=brand_id, word=word)

            return {
                'body': body,
                'provider': result.get('provider', 'unknown'),
                'tokens_used': result.get('tokens_used', 0),
            }

        except Exception as e:
            logger.error("body_generation_failed",
                          brand_id=brand_id, error=str(e))
            return None

    def _select_cta(self, brand_config: dict) -> str:
        """
        Selects a call-to-action from the brand's CTA examples.

        Parameters:
            brand_config: Brand configuration with cta_examples.

        Returns:
            Selected CTA text string.
        """
        import random
        cta_examples = brand_config.get('cta_examples', [])
        if cta_examples:
            return random.choice(cta_examples)
        return "Follow for more."

    def _classify_pillar(self, topic: str, script_text: str,
                          brand_config: dict) -> str:
        """
        Classifies which brand pillar this script belongs to.

        Parameters:
            topic: Script topic.
            script_text: Full script text.
            brand_config: Brand configuration with pillars.

        Returns:
            Best matching pillar string.
        """
        pillars = brand_config.get('pillars', [])
        if not pillars:
            return 'general'

        combined_text = f"{topic} {script_text}".lower()

        best_pillar = pillars[0]
        best_score = 0

        for pillar in pillars:
            pillar_words = pillar.lower().split()
            score = sum(
                1 for word in pillar_words
                if word in combined_text
            )
            if score > best_score:
                best_score = score
                best_pillar = pillar

        return best_pillar

    def _store_script(self, script_data: dict) -> int:
        """
        Stores a generated script in the database.

        Parameters:
            script_data: Dict with all script fields.

        Returns:
            ID of the inserted script row.

        Side effects:
            Inserts a row into the scripts table.
        """
        self.db.execute_write(
            "INSERT INTO scripts "
            "(brand_id, trend_id, hook, hook_type, body, cta, "
            "script_text, word_count, pillar, llm_provider, "
            "llm_tokens_used, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?)",
            (
                script_data['brand_id'],
                script_data.get('trend_id'),
                script_data['hook'],
                script_data['hook_type'],
                script_data['body'],
                script_data['cta'],
                script_data['script_text'],
                script_data['word_count'],
                script_data.get('pillar', ''),
                script_data.get('llm_provider', 'unknown'),
                script_data.get('llm_tokens_used', 0),
                datetime.now(timezone.utc).isoformat(),
            )
        )

        # Get the inserted ID
        row = self.db.fetch_one(
            "SELECT MAX(id) as max_id FROM scripts WHERE brand_id=?",
            (script_data['brand_id'],)
        )
        return row['max_id'] if row else 0

    def get_pending_scripts(self, brand_id: str,
                             limit: int = 5) -> list[dict]:
        """
        Gets scripts in draft status ready for processing.

        Parameters:
            brand_id: Brand identifier.
            limit: Maximum scripts to return.

        Returns:
            List of script dicts.
        """
        rows = self.db.fetch_all(
            "SELECT * FROM scripts "
            "WHERE brand_id=? AND status='draft' "
            "ORDER BY created_at DESC LIMIT ?",
            (brand_id, limit)
        )
        return [dict(row) for row in rows]
