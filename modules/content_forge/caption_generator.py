"""
Caption Generator — creates subtitles and platform-specific captions.

Uses Kokoro TTS word-level timestamps to produce:
  * SRT subtitle files
  * VTT subtitle files
  * Platform-specific caption text (respecting max-length rules)
  * LLM-varied captions per platform via ``LLMRouter``

Platform max lengths:
  TikTok    : 150 chars
  Instagram : 2 200 chars
  Facebook  : 2 000 chars (recommended)
  YouTube   : 5 000 chars
  Snapchat  : 80 chars
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("autofarm.content_forge.caption_generator")

# ---------------------------------------------------------------------------
# Platform caption limits
# ---------------------------------------------------------------------------

PLATFORM_CAPTION_LIMITS: Dict[str, int] = {
    "tiktok": 150,
    "instagram": 2200,
    "facebook": 2000,
    "youtube": 5000,
    "snapchat": 80,
}


# ---------------------------------------------------------------------------
# CaptionGenerator
# ---------------------------------------------------------------------------


class CaptionGenerator:
    """Generate subtitles and platform-specific captions.

    Parameters
    ----------
    llm_router:
        ``LLMRouter`` for caption variation generation.
    media_root:
        Root directory for output files.
    """

    def __init__(
        self,
        llm_router: Any,
        media_root: str = "media",
    ) -> None:
        self.llm_router = llm_router
        self.media_root = Path(media_root)

    # ------------------------------------------------------------------
    # Public API — subtitle generation
    # ------------------------------------------------------------------

    def generate_subtitles(
        self,
        word_timestamps: List[Dict[str, Any]],
        brand_id: str,
        output_dir: Optional[str] = None,
    ) -> Dict[str, str]:
        """Generate SRT and VTT subtitle files from word timestamps.

        Parameters
        ----------
        word_timestamps:
            List of ``{"word": str, "start": float, "end": float}`` dicts
            from the TTS engine.
        brand_id:
            Brand identifier.
        output_dir:
            Output directory; auto-determined if ``None``.

        Returns
        -------
        Dict[str, str]
            ``{"srt": path, "vtt": path, "timing_data": json_path}``.
        """
        if output_dir is None:
            out = self.media_root / "captions" / brand_id
        else:
            out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        # Group words into subtitle segments (max ~6 words per line)
        segments = self._group_into_segments(word_timestamps, max_words=6)

        srt_path = str(out / "subtitles.srt")
        vtt_path = str(out / "subtitles.vtt")

        self._write_srt(segments, srt_path)
        self._write_vtt(segments, vtt_path)

        # Write raw timing data
        import json
        timing_path = str(out / "timing_data.json")
        with open(timing_path, "w") as f:
            json.dump(word_timestamps, f, indent=2)

        return {"srt": srt_path, "vtt": vtt_path, "timing_data": timing_path}

    # ------------------------------------------------------------------
    # Public API — platform captions
    # ------------------------------------------------------------------

    async def generate_platform_caption(
        self,
        script_text: str,
        brand_id: str,
        platform: str,
        hashtags: Optional[List[str]] = None,
    ) -> str:
        """Generate a platform-specific caption from the script.

        Parameters
        ----------
        script_text:
            The full script body.
        brand_id:
            Brand identifier.
        platform:
            Target platform name.
        hashtags:
            Optional list of hashtags to append.

        Returns
        -------
        str
            Platform-compliant caption text.
        """
        max_len = PLATFORM_CAPTION_LIMITS.get(platform, 2000)

        # Use LLM to create platform-appropriate variation
        varied = await self.vary_caption_for_platform(
            script_text, platform, brand_id
        )

        # Append hashtags
        if hashtags:
            hashtag_str = " " + " ".join(f"#{h}" for h in hashtags)
            if len(varied) + len(hashtag_str) <= max_len:
                varied += hashtag_str
            else:
                # Truncate caption to make room for hashtags
                available = max_len - len(hashtag_str) - 3
                if available > 20:
                    varied = varied[:available] + "..." + hashtag_str

        # Final truncation safety
        if len(varied) > max_len:
            varied = varied[: max_len - 3] + "..."

        return varied

    async def vary_caption_for_platform(
        self,
        base_text: str,
        platform: str,
        brand_id: str,
    ) -> str:
        """Create a platform-specific caption variant using LLMRouter.

        Parameters
        ----------
        base_text:
            Source script / caption text.
        platform:
            Target platform.
        brand_id:
            Brand identifier.

        Returns
        -------
        str
            Rewritten caption for the platform.
        """
        max_len = PLATFORM_CAPTION_LIMITS.get(platform, 2000)

        prompt = (
            f"Rewrite the following script excerpt as a short, engaging {platform} "
            f"caption. Maximum {max_len} characters. Make it punchy, include a "
            "hook in the first line, and add a call-to-action at the end. "
            "Do NOT include hashtags — they will be added separately.\n\n"
            f"SCRIPT:\n{base_text[:500]}\n\n"
            "CAPTION:"
        )

        try:
            caption = await self.llm_router.generate(
                prompt=prompt,
                task="caption_variation",
                brand_id=brand_id,
            )
            caption = caption.strip().strip('"').strip("'")
            if len(caption) > max_len:
                caption = caption[: max_len - 3] + "..."
            return caption
        except Exception as exc:
            logger.warning("LLM caption variation failed: %s — using fallback", exc)
            return self._simple_caption_fallback(base_text, max_len)

    def check_caption_compliance(
        self, caption: str, platform: str
    ) -> List[str]:
        """Check a caption for platform compliance violations.

        Parameters
        ----------
        caption:
            Caption text to check.
        platform:
            Target platform.

        Returns
        -------
        List[str]
            List of violation descriptions (empty = compliant).
        """
        violations: List[str] = []
        max_len = PLATFORM_CAPTION_LIMITS.get(platform, 2000)

        if len(caption) > max_len:
            violations.append(
                f"Caption exceeds {platform} limit: {len(caption)} > {max_len} chars"
            )

        if not caption.strip():
            violations.append("Caption is empty")

        # Check for excessive hashtags
        hashtag_count = caption.count("#")
        if platform == "tiktok" and hashtag_count > 5:
            violations.append(
                f"Too many hashtags for TikTok: {hashtag_count} (max 5)"
            )
        if platform == "instagram" and hashtag_count > 30:
            violations.append(
                f"Too many hashtags for Instagram: {hashtag_count} (max 30)"
            )

        # Check for banned patterns
        if re.search(r"(click\s+the\s+link\s+in\s+bio)", caption, re.IGNORECASE):
            if platform == "tiktok":
                violations.append("TikTok discourages 'link in bio' phrasing")

        return violations

    # ------------------------------------------------------------------
    # Private — subtitle helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _group_into_segments(
        word_timestamps: List[Dict[str, Any]],
        max_words: int = 6,
    ) -> List[Dict[str, Any]]:
        """Group word timestamps into subtitle segments.

        Parameters
        ----------
        word_timestamps:
            List of ``{"word", "start", "end"}`` dicts.
        max_words:
            Maximum words per subtitle segment.

        Returns
        -------
        List[Dict[str, Any]]
            Each segment has ``text``, ``start``, ``end`` keys.
        """
        segments: List[Dict[str, Any]] = []
        buffer: List[Dict[str, Any]] = []

        for wt in word_timestamps:
            buffer.append(wt)
            if len(buffer) >= max_words:
                segments.append({
                    "text": " ".join(w["word"] for w in buffer),
                    "start": buffer[0]["start"],
                    "end": buffer[-1]["end"],
                })
                buffer = []

        if buffer:
            segments.append({
                "text": " ".join(w["word"] for w in buffer),
                "start": buffer[0]["start"],
                "end": buffer[-1]["end"],
            })

        return segments

    @staticmethod
    def _format_srt_time(seconds: float) -> str:
        """Format seconds as SRT timestamp (HH:MM:SS,mmm).

        Parameters
        ----------
        seconds:
            Time in seconds.

        Returns
        -------
        str
            SRT-formatted timestamp.
        """
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int((seconds % 1) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    @staticmethod
    def _format_vtt_time(seconds: float) -> str:
        """Format seconds as VTT timestamp (HH:MM:SS.mmm).

        Parameters
        ----------
        seconds:
            Time in seconds.

        Returns
        -------
        str
            VTT-formatted timestamp.
        """
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int((seconds % 1) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"

    def _write_srt(
        self, segments: List[Dict[str, Any]], path: str
    ) -> None:
        """Write subtitle segments to an SRT file.

        Parameters
        ----------
        segments:
            Subtitle segments.
        path:
            Output file path.
        """
        with open(path, "w", encoding="utf-8") as f:
            for i, seg in enumerate(segments, 1):
                start = self._format_srt_time(seg["start"])
                end = self._format_srt_time(seg["end"])
                f.write(f"{i}\n{start} --> {end}\n{seg['text']}\n\n")

    def _write_vtt(
        self, segments: List[Dict[str, Any]], path: str
    ) -> None:
        """Write subtitle segments to a VTT file.

        Parameters
        ----------
        segments:
            Subtitle segments.
        path:
            Output file path.
        """
        with open(path, "w", encoding="utf-8") as f:
            f.write("WEBVTT\n\n")
            for i, seg in enumerate(segments, 1):
                start = self._format_vtt_time(seg["start"])
                end = self._format_vtt_time(seg["end"])
                f.write(f"{i}\n{start} --> {end}\n{seg['text']}\n\n")

    # ------------------------------------------------------------------
    # Private — fallback
    # ------------------------------------------------------------------

    @staticmethod
    def _simple_caption_fallback(text: str, max_len: int) -> str:
        """Create a simple caption from the first sentence of the script.

        Parameters
        ----------
        text:
            Full script body.
        max_len:
            Maximum character count.

        Returns
        -------
        str
            Truncated caption.
        """
        sentences = re.split(r"[.!?]+", text.strip())
        first = sentences[0].strip() if sentences else text.strip()
        if len(first) > max_len:
            return first[: max_len - 3] + "..."
        return first
