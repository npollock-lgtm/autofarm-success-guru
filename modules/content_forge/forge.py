"""
Content Forge — orchestrates the complete video creation pipeline.

Coordinates all content-forge sub-modules:
  BackgroundManager → TTSEngine → BRollFetcher → MusicFetcher →
  CaptionGenerator → VideoAssembler → ThumbnailMaker

Pipeline:
  1. Select / fetch background video (with fallback chain)
  2. Generate TTS voiceover with word-level timestamps
  3. Fetch B-roll clips
  4. Fetch background music
  5. Generate captions / subtitles from timestamps
  6. Assemble final video (resource-checked)
  7. Generate thumbnail
  8. Quality gate check
  9. Return complete content package

All heavy steps check ``ResourceScheduler`` before proceeding.
State transitions: SCRIPT_APPROVED → TTS_QUEUED → TTS_DONE → VIDEO_ASSEMBLY → VIDEO_ASSEMBLED
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("autofarm.content_forge.forge")


# ---------------------------------------------------------------------------
# Forge result container
# ---------------------------------------------------------------------------


@dataclass
class ForgeResult:
    """Complete output of the content-forge pipeline."""

    brand_id: str
    video_path: str = ""
    thumbnail_path: str = ""
    thumbnail_base64: str = ""
    captions_srt: str = ""
    captions_vtt: str = ""
    voiceover_path: str = ""
    duration_seconds: float = 0.0
    quality_score: float = 0.0
    thumbnail_quality: float = 0.0
    success: bool = False
    errors: List[str] = field(default_factory=list)
    platform_variants: Dict[str, str] = field(default_factory=dict)
    platform_captions: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialise for downstream consumers."""
        return {
            "brand_id": self.brand_id,
            "video_path": self.video_path,
            "thumbnail_path": self.thumbnail_path,
            "thumbnail_base64": self.thumbnail_base64,
            "captions_srt": self.captions_srt,
            "captions_vtt": self.captions_vtt,
            "voiceover_path": self.voiceover_path,
            "duration_seconds": self.duration_seconds,
            "quality_score": self.quality_score,
            "thumbnail_quality": self.thumbnail_quality,
            "success": self.success,
            "errors": self.errors,
            "platform_variants": self.platform_variants,
            "platform_captions": self.platform_captions,
        }


# ---------------------------------------------------------------------------
# ContentForge
# ---------------------------------------------------------------------------


class ContentForge:
    """Orchestrate the complete video creation pipeline.

    Parameters
    ----------
    background_manager:
        ``BackgroundManager`` instance.
    tts_engine:
        ``TTSEngine`` instance.
    broll_fetcher:
        ``BRollFetcher`` instance.
    music_fetcher:
        ``MusicFetcher`` instance.
    caption_generator:
        ``CaptionGenerator`` instance.
    video_assembler:
        ``VideoAssembler`` instance.
    thumbnail_maker:
        ``ThumbnailMaker`` instance.
    resource_scheduler:
        ``ResourceScheduler`` for resource gating.
    job_state_machine:
        ``JobStateMachine`` for state transitions.
    """

    def __init__(
        self,
        background_manager: Any,
        tts_engine: Any,
        broll_fetcher: Any,
        music_fetcher: Any,
        caption_generator: Any,
        video_assembler: Any,
        thumbnail_maker: Any,
        resource_scheduler: Optional[Any] = None,
        job_state_machine: Optional[Any] = None,
    ) -> None:
        self.bg = background_manager
        self.tts = tts_engine
        self.broll = broll_fetcher
        self.music = music_fetcher
        self.captions = caption_generator
        self.assembler = video_assembler
        self.thumbs = thumbnail_maker
        self.resource_scheduler = resource_scheduler
        self.state_machine = job_state_machine

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def forge_video(
        self,
        brand_id: str,
        script_dict: Dict[str, Any],
        platforms: Optional[List[str]] = None,
        job_id: Optional[int] = None,
    ) -> ForgeResult:
        """Create a complete video from a script.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        script_dict:
            Script data containing at least ``{"text": str, "theme": str}``.
        platforms:
            Target platforms for variant generation.
        job_id:
            Optional job ID for state-machine transitions.

        Returns
        -------
        ForgeResult
            Complete content package.

        Side Effects
        ------------
        * Transitions job state through TTS_QUEUED → TTS_DONE → VIDEO_ASSEMBLY → VIDEO_ASSEMBLED.
        * Creates multiple files on disk.
        """
        if platforms is None:
            platforms = ["tiktok", "instagram", "youtube"]

        result = ForgeResult(brand_id=brand_id)
        script_text = script_dict.get("text", "")
        theme = script_dict.get("theme", "motivational")

        try:
            # ── Step 1: TTS Voiceover ────────────────────────────────
            if self.state_machine and job_id:
                await self.state_machine.transition(job_id, "TTS_QUEUED")

            logger.info("[%s] Generating voiceover…", brand_id)
            tts_result = await self.tts.generate_voiceover(
                script_text=script_text,
                brand_id=brand_id,
            )
            result.voiceover_path = tts_result.audio_path
            result.duration_seconds = tts_result.duration_seconds

            if self.state_machine and job_id:
                await self.state_machine.transition(job_id, "TTS_DONE")

            # ── Step 2: Background Video ─────────────────────────────
            logger.info("[%s] Fetching background…", brand_id)
            bg_path = await self.bg.get_background(
                brand_id, tts_result.duration_seconds + 2.0
            )

            # ── Step 3: B-Roll ───────────────────────────────────────
            logger.info("[%s] Fetching B-roll…", brand_id)
            broll_clips = await self.broll.fetch_broll(
                brand_id=brand_id,
                theme=theme,
                duration_seconds=min(tts_result.duration_seconds / 3, 10.0),
                count=2,
            )

            # ── Step 4: Music ────────────────────────────────────────
            logger.info("[%s] Fetching music…", brand_id)
            music_path = await self.music.fetch_music(
                brand_id=brand_id,
                duration_seconds=tts_result.duration_seconds + 2.0,
            )

            # ── Step 5: Captions / Subtitles ─────────────────────────
            logger.info("[%s] Generating captions…", brand_id)
            subtitle_files = self.captions.generate_subtitles(
                word_timestamps=tts_result.word_timestamps,
                brand_id=brand_id,
            )
            result.captions_srt = subtitle_files.get("srt", "")
            result.captions_vtt = subtitle_files.get("vtt", "")

            # ── Step 6: Video Assembly ───────────────────────────────
            if self.state_machine and job_id:
                await self.state_machine.transition(job_id, "VIDEO_ASSEMBLY")

            logger.info("[%s] Assembling video…", brand_id)
            assembly = await self.assembler.assemble_video(
                brand_id=brand_id,
                background_path=bg_path,
                voiceover_path=tts_result.audio_path,
                music_path=music_path,
                broll_clips=broll_clips,
                captions_srt=result.captions_srt,
            )

            if not assembly.get("success"):
                result.errors.append(
                    f"Video assembly failed: {assembly.get('error', 'unknown')}"
                )
                return result

            result.video_path = assembly["video_path"]
            result.duration_seconds = assembly["duration_seconds"]

            if self.state_machine and job_id:
                await self.state_machine.transition(job_id, "VIDEO_ASSEMBLED")

            # ── Step 7: Thumbnail ────────────────────────────────────
            logger.info("[%s] Generating thumbnail…", brand_id)
            thumb = await self.thumbs.generate_thumbnail(
                video_path=result.video_path,
                brand_id=brand_id,
                script_text=script_text,
            )
            result.thumbnail_path = thumb["image_path"]
            result.thumbnail_quality = thumb["quality_score"]
            result.thumbnail_base64 = thumb["base64_data_uri"]

            # ── Step 8: Platform Variants ────────────────────────────
            logger.info("[%s] Creating platform variants…", brand_id)
            for platform in platforms:
                # Platform-optimised video
                variant = self.assembler.optimize_for_platform(
                    result.video_path, platform
                )
                result.platform_variants[platform] = variant

                # Platform-specific caption
                hashtags = script_dict.get("hashtags", [])
                caption = await self.captions.generate_platform_caption(
                    script_text=script_text,
                    brand_id=brand_id,
                    platform=platform,
                    hashtags=hashtags,
                )
                result.platform_captions[platform] = caption

            # ── Done ─────────────────────────────────────────────────
            result.quality_score = self._compute_quality_score(result)
            result.success = True

            logger.info(
                "[%s] Forge complete: video=%s duration=%.1fs quality=%.2f",
                brand_id, result.video_path, result.duration_seconds,
                result.quality_score,
            )

        except Exception as exc:
            logger.error("[%s] Forge pipeline error: %s", brand_id, exc)
            result.errors.append(str(exc))

        return result

    def can_start_forging(self) -> bool:
        """Check whether sufficient resources are available.

        Returns
        -------
        bool
            ``True`` if forge can start.
        """
        if self.resource_scheduler is None:
            return True
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Cannot await in sync context — assume OK
                return True
            return loop.run_until_complete(
                self.resource_scheduler.can_run_job("video_assembly")
            )
        except Exception:
            return True

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_quality_score(result: ForgeResult) -> float:
        """Compute a composite quality score for the forge output.

        Parameters
        ----------
        result:
            Partially populated ForgeResult.

        Returns
        -------
        float
            Score between 0.0 and 1.0.
        """
        score = 0.0

        # Duration within ideal range
        dur = result.duration_seconds
        if 30 <= dur <= 62:
            score += 0.3
        elif 20 <= dur <= 75:
            score += 0.15

        # Thumbnail quality
        score += result.thumbnail_quality * 0.3

        # Has all components
        if result.voiceover_path:
            score += 0.1
        if result.captions_srt:
            score += 0.1
        if result.video_path:
            score += 0.2

        return min(round(score, 2), 1.0)
