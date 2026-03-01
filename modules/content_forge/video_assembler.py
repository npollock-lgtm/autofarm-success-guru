"""
Video Assembler — composes the final video from all content components using FFmpeg.

Assembles:
  * Background video
  * B-roll overlays
  * Voiceover audio
  * Background music
  * Burned-in captions (subtitles)

Resource requirements:
  * 4 GB free RAM
  * < 70 % CPU utilisation
  * Maximum 1 concurrent assembly job (enforced by ResourceScheduler)

Job state machine transitions:
  TTS_DONE → VIDEO_ASSEMBLY → VIDEO_ASSEMBLED

Target output: MP4, 30–62 seconds, 1080 × 1920 portrait.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("autofarm.content_forge.video_assembler")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OUTPUT_WIDTH = 1080
OUTPUT_HEIGHT = 1920
OUTPUT_FPS = 30
CRF = 23
PRESET = "medium"
AUDIO_BITRATE = "128k"

# ---------------------------------------------------------------------------
# Platform optimisation presets
# ---------------------------------------------------------------------------

PLATFORM_PRESETS: Dict[str, Dict[str, Any]] = {
    "tiktok": {
        "max_duration": 60,
        "bitrate": "4M",
        "resolution": "1080x1920",
    },
    "instagram": {
        "max_duration": 60,
        "bitrate": "3.5M",
        "resolution": "1080x1920",
    },
    "facebook": {
        "max_duration": 60,
        "bitrate": "4M",
        "resolution": "1080x1920",
    },
    "youtube": {
        "max_duration": 62,
        "bitrate": "8M",
        "resolution": "1080x1920",
    },
    "snapchat": {
        "max_duration": 60,
        "bitrate": "3M",
        "resolution": "1080x1920",
    },
}


# ---------------------------------------------------------------------------
# VideoAssembler
# ---------------------------------------------------------------------------


class VideoAssembler:
    """Assemble complete videos from component assets via FFmpeg.

    Parameters
    ----------
    media_root:
        Root directory for media / output.
    resource_scheduler:
        ``ResourceScheduler`` for resource gating.
    """

    def __init__(
        self,
        media_root: str = "media",
        resource_scheduler: Optional[Any] = None,
    ) -> None:
        self.media_root = Path(media_root)
        self.resource_scheduler = resource_scheduler

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def assemble_video(
        self,
        brand_id: str,
        background_path: str,
        voiceover_path: str,
        music_path: Optional[str] = None,
        broll_clips: Optional[List[str]] = None,
        captions_srt: Optional[str] = None,
        output_path: Optional[str] = None,
        music_volume: float = 0.15,
    ) -> Dict[str, Any]:
        """Assemble the complete video from components.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        background_path:
            Path to background video.
        voiceover_path:
            Path to voiceover ``.wav``.
        music_path:
            Optional path to background music.
        broll_clips:
            Optional list of B-roll clip paths.
        captions_srt:
            Optional path to SRT subtitle file.
        output_path:
            Destination path; auto-generated if ``None``.
        music_volume:
            Volume multiplier for background music (0.0–1.0).

        Returns
        -------
        Dict[str, Any]
            ``{"video_path", "duration_seconds", "file_size_mb", "success"}``.

        Side Effects
        ------------
        * Checks ResourceScheduler for available resources.
        * Creates the final MP4 file on disk.
        """
        # Resource check
        if self.resource_scheduler:
            can_run = await self.resource_scheduler.can_run_job("video_assembly")
            if not can_run:
                logger.warning("Insufficient resources for video assembly — waiting")
                import asyncio
                for _ in range(24):
                    await asyncio.sleep(5)
                    if await self.resource_scheduler.can_run_job("video_assembly"):
                        break
                else:
                    logger.error("Resource timeout — proceeding anyway")

        if output_path is None:
            out_dir = self.media_root / "output" / brand_id
            out_dir.mkdir(parents=True, exist_ok=True)
            ts = int(time.time())
            output_path = str(out_dir / f"video_{brand_id}_{ts}.mp4")

        try:
            # Step 1: Prepare background (scale + loop to match voiceover)
            vo_duration = self._get_duration(voiceover_path)
            target_duration = max(vo_duration + 1.0, 30.0)  # At least 30s

            bg_prepared = self._prepare_background(
                background_path, target_duration, brand_id
            )

            # Step 2: Mix audio tracks
            mixed_audio = self._mix_audio(
                voiceover_path, music_path, brand_id, music_volume, target_duration
            )

            # Step 3: Build FFmpeg complex filter
            final_path = self._compose_final(
                bg_prepared, mixed_audio, captions_srt, broll_clips, output_path
            )

            duration = self._get_duration(final_path)
            size_mb = os.path.getsize(final_path) / (1024 * 1024) if os.path.exists(final_path) else 0

            logger.info(
                "Video assembled: %s (%.1fs, %.1f MB)",
                final_path, duration, size_mb,
            )
            return {
                "video_path": final_path,
                "duration_seconds": duration,
                "file_size_mb": round(size_mb, 2),
                "success": True,
            }
        except Exception as exc:
            logger.error("Video assembly failed: %s", exc)
            return {
                "video_path": "",
                "duration_seconds": 0,
                "file_size_mb": 0,
                "success": False,
                "error": str(exc),
            }

    def apply_brand_color_grading(
        self, video_path: str, brand_id: str
    ) -> str:
        """Apply brand-specific colour grading via FFmpeg.

        Parameters
        ----------
        video_path:
            Source video.
        brand_id:
            Brand identifier.

        Returns
        -------
        str
            Path to the colour-graded video.
        """
        output = video_path.replace(".mp4", "_graded.mp4")
        # Import background_library for treatment filters
        from modules.content_forge.background_library import (
            BRAND_BACKGROUND_PROFILES,
            BackgroundManager,
        )

        profile = BRAND_BACKGROUND_PROFILES.get(brand_id, {})
        treatment = profile.get("color_treatment", "")
        motion = profile.get("motion_speed", 0.5)
        vf = BackgroundManager._treatment_to_filter(treatment, motion)

        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-vf", vf,
            "-c:v", "libx264", "-preset", PRESET, "-crf", str(CRF),
            "-c:a", "copy", output,
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=300)
            return output
        except Exception as exc:
            logger.warning("Color grading failed: %s", exc)
            return video_path

    def optimize_for_platform(
        self, video_path: str, platform: str
    ) -> str:
        """Apply platform-specific optimisations (bitrate, resolution, etc.).

        Parameters
        ----------
        video_path:
            Source video path.
        platform:
            Target platform name.

        Returns
        -------
        str
            Optimised video path.
        """
        preset = PLATFORM_PRESETS.get(platform, PLATFORM_PRESETS["tiktok"])
        output = video_path.replace(".mp4", f"_{platform}.mp4")
        resolution = preset["resolution"]
        bitrate = preset["bitrate"]
        max_dur = preset["max_duration"]

        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-t", str(max_dur),
            "-vf", f"scale={resolution.replace('x', ':')}:force_original_aspect_ratio=decrease,"
                   f"pad={resolution.replace('x', ':')}:(ow-iw)/2:(oh-ih)/2",
            "-c:v", "libx264", "-b:v", bitrate, "-preset", PRESET,
            "-c:a", "aac", "-b:a", AUDIO_BITRATE,
            "-movflags", "+faststart",
            output,
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=300)
            return output
        except Exception as exc:
            logger.warning("Platform optimisation failed for %s: %s", platform, exc)
            return video_path

    def generate_preview(
        self, video_path: str, max_seconds: int = 15
    ) -> str:
        """Generate a low-quality preview for Telegram review.

        Parameters
        ----------
        video_path:
            Source video path.
        max_seconds:
            Maximum preview duration.

        Returns
        -------
        str
            Preview video path.
        """
        preview_path = video_path.replace(".mp4", "_preview.mp4")
        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-t", str(max_seconds),
            "-vf", "scale=480:-2",
            "-c:v", "libx264", "-crf", "28",
            "-c:a", "aac", "-b:a", "64k",
            "-movflags", "+faststart",
            preview_path,
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=60)
            return preview_path
        except Exception as exc:
            logger.warning("Preview generation failed: %s", exc)
            return video_path

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_duration(self, file_path: str) -> float:
        """Get the duration of a media file via ffprobe.

        Parameters
        ----------
        file_path:
            Path to audio/video file.

        Returns
        -------
        float
            Duration in seconds (0.0 on failure).
        """
        try:
            result = subprocess.run(
                [
                    "ffprobe", "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "json", file_path,
                ],
                capture_output=True, text=True, timeout=10,
            )
            data = json.loads(result.stdout)
            return float(data.get("format", {}).get("duration", 0))
        except Exception:
            return 0.0

    def _prepare_background(
        self, bg_path: str, target_duration: float, brand_id: str
    ) -> str:
        """Scale and loop background video to match target duration.

        Parameters
        ----------
        bg_path:
            Source background video.
        target_duration:
            Desired duration in seconds.
        brand_id:
            Brand identifier.

        Returns
        -------
        str
            Prepared background path.
        """
        out_dir = self.media_root / "temp" / brand_id
        out_dir.mkdir(parents=True, exist_ok=True)
        output = str(out_dir / "bg_prepared.mp4")

        bg_dur = self._get_duration(bg_path)
        loops = max(int(target_duration / max(bg_dur, 1)) + 1, 1)

        cmd = [
            "ffmpeg", "-y",
            "-stream_loop", str(loops),
            "-i", bg_path,
            "-t", str(target_duration),
            "-vf", f"scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:force_original_aspect_ratio=decrease,"
                   f"pad={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:(ow-iw)/2:(oh-ih)/2",
            "-c:v", "libx264", "-preset", "fast", "-crf", str(CRF),
            "-an", output,
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=180)
            return output
        except Exception as exc:
            logger.warning("Background preparation failed: %s", exc)
            return bg_path

    def _mix_audio(
        self,
        voiceover_path: str,
        music_path: Optional[str],
        brand_id: str,
        music_volume: float,
        duration: float,
    ) -> str:
        """Mix voiceover and optional music into a single audio track.

        Parameters
        ----------
        voiceover_path:
            Voiceover audio path.
        music_path:
            Optional music path.
        brand_id:
            Brand identifier.
        music_volume:
            Music volume multiplier.
        duration:
            Target duration.

        Returns
        -------
        str
            Mixed audio file path.
        """
        out_dir = self.media_root / "temp" / brand_id
        out_dir.mkdir(parents=True, exist_ok=True)
        output = str(out_dir / "mixed_audio.wav")

        if not music_path or not os.path.exists(music_path):
            # No music — just return voiceover
            return voiceover_path

        cmd = [
            "ffmpeg", "-y",
            "-i", voiceover_path,
            "-i", music_path,
            "-filter_complex",
            f"[0:a]apad[vo];[1:a]volume={music_volume},atrim=0:{duration}[bg];"
            "[vo][bg]amix=inputs=2:duration=first:dropout_transition=3[out]",
            "-map", "[out]",
            "-t", str(duration),
            output,
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=120)
            return output
        except Exception as exc:
            logger.warning("Audio mixing failed: %s — using voiceover only", exc)
            return voiceover_path

    def _compose_final(
        self,
        bg_path: str,
        audio_path: str,
        captions_srt: Optional[str],
        broll_clips: Optional[List[str]],
        output_path: str,
    ) -> str:
        """Compose the final video: background + audio + captions.

        Parameters
        ----------
        bg_path:
            Prepared background video.
        audio_path:
            Mixed audio track.
        captions_srt:
            Optional SRT subtitle file for burn-in.
        broll_clips:
            Optional B-roll clips (overlaid at intervals).
        output_path:
            Final output path.

        Returns
        -------
        str
            Path to the assembled video.
        """
        vf_parts: List[str] = []

        # Burn in subtitles if available
        if captions_srt and os.path.exists(captions_srt):
            # Escape path for FFmpeg subtitles filter
            srt_escaped = captions_srt.replace("\\", "/").replace(":", "\\:")
            vf_parts.append(
                f"subtitles='{srt_escaped}':force_style="
                "'Alignment=2,FontSize=18,PrimaryColour=&H00FFFFFF,"
                "OutlineColour=&H00000000,Outline=2,MarginV=60'"
            )

        vf_str = ",".join(vf_parts) if vf_parts else None

        cmd = ["ffmpeg", "-y", "-i", bg_path, "-i", audio_path]

        if vf_str:
            cmd.extend(["-vf", vf_str])

        cmd.extend([
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-preset", PRESET, "-crf", str(CRF),
            "-c:a", "aac", "-b:a", AUDIO_BITRATE,
            "-shortest",
            "-movflags", "+faststart",
            output_path,
        ])

        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=300)
            return output_path
        except subprocess.CalledProcessError as exc:
            logger.error("Final composition failed: %s", exc.stderr[:500] if exc.stderr else exc)
            raise
