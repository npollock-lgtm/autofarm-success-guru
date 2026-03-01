"""
Caption Writer — generates platform-optimised captions from voiceover scripts.

Supports multiple variations for A/B testing.
All LLM calls go through ``LLMRouter``.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("autofarm.publish_engine.caption_writer")

# Platform character limits
PLATFORM_LIMITS: Dict[str, int] = {
    "tiktok": 2200,
    "instagram": 2200,
    "facebook": 2000,
    "youtube": 5000,
    "snapchat": 250,
}


class CaptionWriter:
    """Generate platform-optimised captions from a script.

    Parameters
    ----------
    llm_router:
        ``LLMRouter`` instance for variation generation.
    """

    def __init__(self, llm_router: Any) -> None:
        self.llm_router = llm_router

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def generate_captions(
        self,
        script: str,
        brand_config: Dict[str, Any],
        platform: str,
        variation_count: int = 1,
    ) -> List[str]:
        """Generate caption(s) for a platform.

        Parameters
        ----------
        script:
            Voiceover script text.
        brand_config:
            Brand identity dict.
        platform:
            Target platform.
        variation_count:
            Number of caption variations.

        Returns
        -------
        List[str]
            Ready-to-publish captions.
        """
        max_len = PLATFORM_LIMITS.get(platform, 2000)
        key_messages = self._extract_key_messages(script)
        hook = key_messages[0] if key_messages else script[:100]

        prompt = (
            f"Write a short, engaging {platform} caption for this video.\n"
            f"Key message: {hook}\n"
            f"Brand voice: {brand_config.get('voice', 'motivational')}\n"
            f"Max length: {max_len} characters.\n"
            "Include a hook and a call-to-action. No hashtags.\n"
            "CAPTION:"
        )

        base_caption = ""
        try:
            raw = await self.llm_router.generate(
                prompt=prompt,
                task="caption_generation",
                brand_id=brand_config.get("brand_id", ""),
            )
            base_caption = raw.strip().strip('"').strip("'")
        except Exception as exc:
            logger.warning("LLM caption generation failed: %s", exc)
            base_caption = self._fallback_caption(script, max_len)

        base_caption = self._optimize_for_platform_limits(base_caption, platform)
        captions = [base_caption]

        if variation_count > 1:
            variants = await self._generate_variations(
                base_caption, brand_config, variation_count - 1
            )
            captions.extend(variants)

        return captions

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_key_messages(script: str) -> List[str]:
        """Identify 1–3 key hooks / CTAs from the script.

        Parameters
        ----------
        script:
            Script body.

        Returns
        -------
        List[str]
        """
        sentences = re.split(r"[.!?]+", script.strip())
        sentences = [s.strip() for s in sentences if len(s.strip()) > 10]
        # First sentence = hook, last = CTA
        keys: List[str] = []
        if sentences:
            keys.append(sentences[0])
        if len(sentences) > 1:
            keys.append(sentences[-1])
        if len(sentences) > 3:
            keys.append(sentences[len(sentences) // 2])
        return keys

    @staticmethod
    def _optimize_for_platform_limits(caption: str, platform: str) -> str:
        """Truncate / format caption for platform limits.

        Parameters
        ----------
        caption:
            Raw caption.
        platform:
            Target platform.

        Returns
        -------
        str
        """
        max_len = PLATFORM_LIMITS.get(platform, 2000)
        if len(caption) <= max_len:
            return caption
        return caption[: max_len - 3] + "..."

    async def _generate_variations(
        self,
        base_caption: str,
        brand_config: Dict[str, Any],
        count: int,
    ) -> List[str]:
        """Generate alternative wordings using LLM.

        Parameters
        ----------
        base_caption:
            Original caption.
        brand_config:
            Brand identity dict.
        count:
            Number of variations.

        Returns
        -------
        List[str]
        """
        variations: List[str] = []
        for i in range(count):
            prompt = (
                f"Rewrite this caption in a slightly different tone "
                f"(variation {i + 1}):\n\n{base_caption}\n\n"
                f"Brand voice: {brand_config.get('voice', 'motivational')}\n"
                "Keep the same meaning but change the wording.\nVARIATION:"
            )
            try:
                raw = await self.llm_router.generate(
                    prompt=prompt,
                    task="caption_variation",
                    brand_id=brand_config.get("brand_id", ""),
                )
                variations.append(raw.strip().strip('"').strip("'"))
            except Exception:
                variations.append(base_caption)
        return variations

    @staticmethod
    def _fallback_caption(script: str, max_len: int) -> str:
        """Create a simple caption from the first sentence.

        Parameters
        ----------
        script:
            Script body.
        max_len:
            Maximum length.

        Returns
        -------
        str
        """
        sentences = re.split(r"[.!?]+", script.strip())
        first = sentences[0].strip() if sentences else script[:max_len]
        if len(first) > max_len:
            return first[: max_len - 3] + "..."
        return first
