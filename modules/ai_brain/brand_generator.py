"""
Brand configuration auto-generator for AutoFarm Zero — Success Guru Network v6.0.

Uses Groq (70B model) to generate complete brand configurations from
a brief description. This is a rare, complex task that uses the
higher-capability Groq model rather than local Ollama.

Generates complete brand identity including: positioning, pillars,
visual identity, voice persona, hook priority, CTA examples, series
formats, premium rules, subreddits, and affiliate categories.

Used by scripts/add_brand.py for zero-touch brand expansion.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

import structlog

from database.db import Database
from modules.ai_brain.llm_router import LLMRouter

logger = structlog.get_logger(__name__)


class BrandConfigGenerator:
    """
    Uses Groq to generate a complete brand configuration from a brief description.

    Generates: positioning, pillars, visual identity, voice persona, hook priority,
    CTA examples, series formats, premium rules, subreddits, affiliate categories.

    The generation uses the Groq 70B model for its superior instruction-following
    and structured output capabilities. This is a rare operation (at most once
    per month when adding a new brand).

    Attributes:
        GENERATION_PROMPT: System prompt for brand config generation.
        REQUIRED_FIELDS: Fields that must be present in generated config.
    """

    GENERATION_PROMPT: str = """You are a brand strategy expert and digital marketing specialist.
Create a complete brand identity for a faceless social media channel in the
Success Guru Network. The network produces premium, psychology-rooted,
authoritative self-improvement content.

Generate a COMPLETE brand configuration as a JSON object with this exact schema:

{
    "display_name": "Brand Display Name",
    "niche": "Brief niche description",
    "positioning": "2-3 sentence brand positioning statement",
    "pillars": ["pillar1", "pillar2", "pillar3", "pillar4", "pillar5"],
    "visual_identity": {
        "primary_color": "#hex",
        "secondary_color": "#hex",
        "accent_color": "#hex",
        "font_style": "serif or sans-serif",
        "overlay_opacity": 0.0-1.0,
        "text_position": "center or lower_third"
    },
    "voice_persona": {
        "tone": "e.g., authoritative, calm, strategic",
        "pacing": "e.g., measured, deliberate, energetic",
        "vocabulary_level": "e.g., sophisticated, accessible, academic",
        "kokoro_voice": "one of: af_sky, am_adam, bf_emma, am_michael, af_bella",
        "kokoro_speed": 0.75-1.0,
        "forbidden_words": ["word1", "word2", "word3", "word4", "word5", "word6"],
        "signature_phrases": ["phrase1", "phrase2", "phrase3"]
    },
    "hook_priority": ["type1", "type2", "type3", "type4"],
    "cta_examples": ["cta1", "cta2", "cta3"],
    "series_formats": [
        {"name": "Series Name", "description": "What this series covers", "episode_format": "Numbered or themed"}
    ],
    "premium_rules": {
        "max_posts_per_day": 2,
        "min_hours_between_posts": 4,
        "quality_threshold": 0.7,
        "never_post_before_hour_utc": 6,
        "never_post_after_hour_utc": 23
    },
    "trend_sources": {
        "subreddits": ["sub1", "sub2", "sub3", "sub4", "sub5"],
        "news_keywords": ["keyword1", "keyword2", "keyword3"],
        "google_trends_keywords": ["keyword1", "keyword2", "keyword3"]
    },
    "sister_brands": []
}

IMPORTANT:
- The brand must be distinct from existing brands in the network
- Content must be 100% faceless (no face reveal, no personal identity)
- Voice must be premium and authoritative, not casual or clickbaity
- Forbidden words should include overused YouTube/TikTok filler words
- Hook types should be specific to the niche
- Subreddits must be real, active subreddits related to the niche

Respond with JSON only. No preamble, no explanation, no markdown formatting."""

    REQUIRED_FIELDS: list[str] = [
        'display_name', 'niche', 'positioning', 'pillars',
        'visual_identity', 'voice_persona', 'hook_priority',
        'cta_examples', 'trend_sources',
    ]

    def __init__(self) -> None:
        """
        Initializes the BrandConfigGenerator.

        Side effects:
            Creates LLMRouter and Database instances.
        """
        self.llm_router = LLMRouter()
        self.db = Database()

    def generate_brand_config(self, brand_name: str,
                               niche_description: str,
                               existing_brands: Optional[list[str]] = None
                               ) -> dict:
        """
        Generates a complete brand configuration from a description.

        Parameters:
            brand_name: Proposed brand name.
            niche_description: Brief description of the brand's niche.
            existing_brands: List of existing brand IDs to avoid overlap.

        Returns:
            Dict with complete brand configuration, or dict with
            'error' key on failure.

        Side effects:
            Makes LLM call via LLMRouter (uses Groq 70B for quality).
        """
        if existing_brands is None:
            existing_brands = self._get_existing_brands()

        prompt = (
            f"{self.GENERATION_PROMPT}\n\n"
            f"Brand name: {brand_name}\n"
            f"Niche: {niche_description}\n"
            f"Existing brands to differentiate from: "
            f"{', '.join(existing_brands)}\n"
        )

        try:
            result = self.llm_router.generate(
                prompt=prompt,
                task_type='brand_config_generation',
                max_tokens=2000,
                temperature=0.7,
            )

            text = result.get('text', '')
            config = self._parse_config(text)

            if config is None:
                return {
                    'error': 'Failed to parse generated config',
                    'raw_text': text,
                }

            # Validate required fields
            validation = self._validate_config(config)
            if not validation['valid']:
                return {
                    'error': 'Generated config missing fields',
                    'missing_fields': validation['missing'],
                    'config': config,
                }

            # Add metadata
            config['network_name'] = 'Success Guru Network'
            config['generated_at'] = datetime.now(timezone.utc).isoformat()
            config['generated_by'] = result.get('provider', 'unknown')

            logger.info("brand_config_generated",
                          brand_name=brand_name,
                          provider=result.get('provider', 'unknown'),
                          tokens_used=result.get('tokens_used', 0))

            return config

        except Exception as e:
            logger.error("brand_config_generation_failed",
                          brand_name=brand_name, error=str(e))
            return {'error': str(e)}

    def _parse_config(self, text: str) -> Optional[dict]:
        """
        Parses JSON configuration from LLM response text.

        Parameters:
            text: Raw LLM response that should contain JSON.

        Returns:
            Parsed dict or None if parsing fails.
        """
        # Try direct JSON parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try extracting JSON from markdown code block
        if '```json' in text:
            start = text.index('```json') + 7
            end = text.index('```', start)
            try:
                return json.loads(text[start:end].strip())
            except (json.JSONDecodeError, ValueError):
                pass

        # Try extracting first { to last }
        try:
            start = text.index('{')
            end = text.rindex('}') + 1
            return json.loads(text[start:end])
        except (ValueError, json.JSONDecodeError):
            pass

        return None

    def _validate_config(self, config: dict) -> dict:
        """
        Validates that generated config has all required fields.

        Parameters:
            config: Generated brand configuration dict.

        Returns:
            Dict with valid (bool) and missing (list[str]) fields.
        """
        missing = [
            field for field in self.REQUIRED_FIELDS
            if field not in config
        ]
        return {
            'valid': len(missing) == 0,
            'missing': missing,
        }

    def _get_existing_brands(self) -> list[str]:
        """
        Gets list of existing brand IDs from the database.

        Returns:
            List of brand ID strings.

        Side effects:
            Queries the brands table.
        """
        try:
            rows = self.db.fetch_all("SELECT id FROM brands")
            return [row['id'] for row in rows]
        except Exception:
            return []

    def save_brand_config(self, brand_id: str,
                          config: dict) -> bool:
        """
        Saves a generated brand config to brands.json and database.

        Parameters:
            brand_id: Brand identifier (snake_case).
            config: Complete brand configuration dict.

        Returns:
            True if saved successfully, False on error.

        Side effects:
            Updates brands.json file.
            Inserts brand record into database.
        """
        try:
            # Load existing brands.json
            from config.settings import CONFIG_DIR
            brands_path = CONFIG_DIR / 'brands.json'

            with open(brands_path, 'r') as f:
                brands = json.load(f)

            # Add new brand
            brands[brand_id] = config

            # Save back
            with open(brands_path, 'w') as f:
                json.dump(brands, f, indent=2)

            # Insert into database
            self.db.execute_write(
                "INSERT OR IGNORE INTO brands (id, display_name, niche, active) "
                "VALUES (?, ?, ?, 1)",
                (brand_id, config.get('display_name', brand_id),
                 config.get('niche', ''))
            )

            logger.info("brand_config_saved",
                          brand_id=brand_id,
                          display_name=config.get('display_name'))
            return True

        except Exception as e:
            logger.error("brand_config_save_failed",
                          brand_id=brand_id, error=str(e))
            return False
