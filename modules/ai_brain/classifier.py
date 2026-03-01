"""
Content classifier for AutoFarm Zero — Success Guru Network v6.0.

Classifies scripts by niche, topic, pillar, and content type.
Used to ensure content diversity across brands and platforms,
and to match content with appropriate publishing slots.
"""

import json
import logging
from typing import Optional

import structlog

from database.db import Database
from modules.ai_brain.llm_router import LLMRouter

logger = structlog.get_logger(__name__)


class ContentClassifier:
    """
    Classifies content scripts for categorization and routing.

    Uses LLM-based classification to categorize scripts by topic,
    content type, audience segment, and emotional tone. Results
    inform scheduling, A/B testing, and analytics reporting.

    Attributes:
        CONTENT_TYPES: Valid content type categories.
        EMOTIONAL_TONES: Valid emotional tone categories.
    """

    CONTENT_TYPES: list[str] = [
        'educational',      # Teaching a concept or skill
        'inspirational',    # Motivational, empowering
        'contrarian',       # Challenging common beliefs
        'storytelling',     # Narrative-based content
        'listicle',         # List-format tips/rules
        'case_study',       # Specific example or research
        'myth_busting',     # Debunking misconceptions
        'actionable_tips',  # Practical how-to advice
    ]

    EMOTIONAL_TONES: list[str] = [
        'authoritative',    # Expert, confident
        'empathetic',       # Understanding, warm
        'provocative',      # Challenging, bold
        'contemplative',    # Thoughtful, reflective
        'urgent',           # Time-sensitive, important
        'calm',             # Peaceful, measured
    ]

    def __init__(self) -> None:
        """
        Initializes the ContentClassifier.

        Side effects:
            Creates LLMRouter and Database instances.
        """
        self.llm_router = LLMRouter()
        self.db = Database()

    def classify_script(self, script_text: str,
                         brand_id: str,
                         brand_config: dict) -> dict:
        """
        Classifies a script across multiple dimensions.

        Parameters:
            script_text: Full script text to classify.
            brand_id: Brand identifier.
            brand_config: Brand configuration for pillar matching.

        Returns:
            Dict with keys: content_type, emotional_tone, pillar,
            topics (list), audience_segment, estimated_duration_seconds,
            confidence.

        Side effects:
            Makes LLM call via LLMRouter for classification.
            Falls back to keyword-based classification on LLM failure.
        """
        try:
            return self._llm_classify(script_text, brand_id, brand_config)
        except Exception as e:
            logger.warning("llm_classification_failed",
                            brand_id=brand_id, error=str(e))
            return self._keyword_classify(script_text, brand_config)

    def _llm_classify(self, script_text: str,
                       brand_id: str,
                       brand_config: dict) -> dict:
        """
        Uses LLM to classify a script.

        Parameters:
            script_text: Full script text.
            brand_id: Brand identifier.
            brand_config: Brand configuration.

        Returns:
            Classification result dict.

        Side effects:
            Makes LLM call via LLMRouter.
        """
        pillars = brand_config.get('pillars', [])

        prompt = (
            f"Classify this video script. Respond with JSON only.\n\n"
            f"Script:\n{script_text[:500]}\n\n"
            f"Classify with:\n"
            f"- content_type: one of {self.CONTENT_TYPES}\n"
            f"- emotional_tone: one of {self.EMOTIONAL_TONES}\n"
            f"- pillar: best match from {pillars}\n"
            f"- topics: list of 2-3 topic keywords\n"
            f"- audience_segment: e.g., 'career professionals', "
            f"'students', 'entrepreneurs'\n\n"
            f"JSON only, no explanation."
        )

        result = self.llm_router.generate(
            prompt=prompt,
            task_type='content_classification',
            max_tokens=200,
            temperature=0.3,
        )

        text = result.get('text', '')

        # Parse JSON from response
        try:
            classification = json.loads(text)
        except json.JSONDecodeError:
            # Try to extract JSON
            try:
                start = text.index('{')
                end = text.rindex('}') + 1
                classification = json.loads(text[start:end])
            except (ValueError, json.JSONDecodeError):
                raise ValueError(f"Could not parse classification: {text[:200]}")

        # Validate and fill defaults
        classification.setdefault('content_type', 'educational')
        classification.setdefault('emotional_tone', 'authoritative')
        classification.setdefault('pillar', pillars[0] if pillars else 'general')
        classification.setdefault('topics', [])
        classification.setdefault('audience_segment', 'general')

        # Estimate duration from word count
        word_count = len(script_text.split())
        # Average speaking rate: ~150 words per minute for measured pace
        classification['estimated_duration_seconds'] = int(
            (word_count / 150) * 60
        )
        classification['confidence'] = 0.8

        return classification

    def _keyword_classify(self, script_text: str,
                           brand_config: dict) -> dict:
        """
        Keyword-based classification fallback when LLM is unavailable.

        Parameters:
            script_text: Full script text.
            brand_config: Brand configuration.

        Returns:
            Classification result dict.
        """
        text_lower = script_text.lower()
        pillars = brand_config.get('pillars', [])

        # Determine content type by keywords
        content_type = 'educational'
        type_keywords = {
            'contrarian': ['wrong', 'myth', 'lie', 'truth', 'nobody'],
            'inspirational': ['believe', 'achieve', 'dream', 'power', 'strength'],
            'listicle': ['rule', 'step', 'habit', 'principle', 'sign'],
            'storytelling': ['story', 'once', 'imagine', 'picture this'],
            'myth_busting': ['myth', 'debunk', 'actually', 'misconception'],
            'actionable_tips': ['how to', 'try this', 'start', 'practice'],
        }

        best_type_score = 0
        for ctype, keywords in type_keywords.items():
            score = sum(1 for kw in keywords if kw in text_lower)
            if score > best_type_score:
                best_type_score = score
                content_type = ctype

        # Determine emotional tone
        emotional_tone = 'authoritative'
        tone_keywords = {
            'provocative': ['wrong', 'lie', 'dangerous', 'shocking'],
            'calm': ['peace', 'still', 'quiet', 'gentle', 'breath'],
            'empathetic': ['feel', 'understand', 'pain', 'struggle'],
            'urgent': ['now', 'today', 'immediately', 'must'],
            'contemplative': ['consider', 'reflect', 'wonder', 'perhaps'],
        }

        best_tone_score = 0
        for tone, keywords in tone_keywords.items():
            score = sum(1 for kw in keywords if kw in text_lower)
            if score > best_tone_score:
                best_tone_score = score
                emotional_tone = tone

        # Match pillar
        pillar = pillars[0] if pillars else 'general'
        best_pillar_score = 0
        for p in pillars:
            p_words = p.lower().split()
            score = sum(1 for w in p_words if w in text_lower)
            if score > best_pillar_score:
                best_pillar_score = score
                pillar = p

        word_count = len(script_text.split())

        return {
            'content_type': content_type,
            'emotional_tone': emotional_tone,
            'pillar': pillar,
            'topics': [],
            'audience_segment': 'general',
            'estimated_duration_seconds': int((word_count / 150) * 60),
            'confidence': 0.5,
        }

    def classify_and_store(self, script_id: int,
                            script_text: str,
                            brand_id: str,
                            brand_config: dict) -> dict:
        """
        Classifies a script and updates its database record.

        Parameters:
            script_id: ID of the script to classify.
            script_text: Full script text.
            brand_id: Brand identifier.
            brand_config: Brand configuration.

        Returns:
            Classification result dict.

        Side effects:
            Updates the scripts table with classification results.
        """
        classification = self.classify_script(
            script_text, brand_id, brand_config
        )

        # Update script record
        self.db.execute_write(
            "UPDATE scripts SET pillar=?, hook_type=COALESCE(hook_type, ?) "
            "WHERE id=?",
            (classification.get('pillar', ''),
             classification.get('content_type', ''),
             script_id)
        )

        logger.info("script_classified",
                      script_id=script_id,
                      brand_id=brand_id,
                      content_type=classification.get('content_type'),
                      pillar=classification.get('pillar'))

        return classification
