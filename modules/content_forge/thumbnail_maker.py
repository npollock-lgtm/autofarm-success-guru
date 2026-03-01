"""
Thumbnail Maker — generates custom thumbnails for each brand's videos.

Quality threshold: ≥ 0.6 (from QualityGate).

Features:
  * Extracts a key frame from the video
  * Applies brand-specific colour overlay and styling
  * Adds text overlay (hook phrase)
  * Produces a base64 data URI for Telegram review embedding
  * Returns a quality score for gate validation
"""

from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("autofarm.content_forge.thumbnail_maker")

# ---------------------------------------------------------------------------
# Brand thumbnail styles
# ---------------------------------------------------------------------------

BRAND_THUMB_STYLES: Dict[str, Dict[str, Any]] = {
    "human_success_guru": {
        "overlay_color": "#1a1a2e",
        "overlay_opacity": 0.5,
        "text_color": "white",
        "font_size": 64,
        "accent_color": "#e94560",
    },
    "wealth_success_guru": {
        "overlay_color": "#0d0d0d",
        "overlay_opacity": 0.4,
        "text_color": "#ffd700",
        "font_size": 60,
        "accent_color": "#ffd700",
    },
    "zen_success_guru": {
        "overlay_color": "#1a3a2a",
        "overlay_opacity": 0.35,
        "text_color": "#f0f0e8",
        "font_size": 56,
        "accent_color": "#7ec8a0",
    },
    "social_success_guru": {
        "overlay_color": "#0a0a30",
        "overlay_opacity": 0.45,
        "text_color": "#00d4ff",
        "font_size": 62,
        "accent_color": "#00d4ff",
    },
    "habits_success_guru": {
        "overlay_color": "#1a1a1a",
        "overlay_opacity": 0.4,
        "text_color": "#ffffff",
        "font_size": 58,
        "accent_color": "#ff9f43",
    },
    "relationships_success_guru": {
        "overlay_color": "#2a0a1a",
        "overlay_opacity": 0.4,
        "text_color": "#fff0f5",
        "font_size": 58,
        "accent_color": "#ff6b81",
    },
}

OUTPUT_WIDTH = 1080
OUTPUT_HEIGHT = 1920


# ---------------------------------------------------------------------------
# ThumbnailMaker
# ---------------------------------------------------------------------------


class ThumbnailMaker:
    """Generate branded thumbnails for video content.

    Parameters
    ----------
    media_root:
        Root directory for media output.
    """

    def __init__(self, media_root: str = "media") -> None:
        self.media_root = Path(media_root)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def generate_thumbnail(
        self,
        video_path: str,
        brand_id: str,
        script_text: str,
        output_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate a custom thumbnail from a video.

        Parameters
        ----------
        video_path:
            Source video file.
        brand_id:
            Brand identifier.
        script_text:
            Script body (first sentence used for text overlay).
        output_path:
            Explicit output path; auto-generated if ``None``.

        Returns
        -------
        Dict[str, Any]
            ``{"image_path", "quality_score", "base64_data_uri"}``.
        """
        if output_path is None:
            out_dir = self.media_root / "thumbnails" / brand_id
            out_dir.mkdir(parents=True, exist_ok=True)
            ts = int(time.time())
            output_path = str(out_dir / f"thumb_{brand_id}_{ts}.jpg")

        # Step 1: Extract key frame
        frame_path = self._extract_key_frame(video_path, brand_id)

        # Step 2: Apply brand styling
        styled_path = self.apply_brand_styling(frame_path, brand_id, output_path)

        # Step 3: Add text overlay
        hook_text = self._extract_hook(script_text)
        if hook_text:
            final_path = self.embed_text_overlay(styled_path, hook_text, brand_id)
        else:
            final_path = styled_path

        # Step 4: Score quality
        quality = self._score_thumbnail(final_path)

        # Step 5: Generate base64 data URI
        b64 = self._to_base64_data_uri(final_path)

        logger.info(
            "Thumbnail generated for %s: quality=%.2f path=%s",
            brand_id, quality, final_path,
        )

        return {
            "image_path": final_path,
            "quality_score": quality,
            "base64_data_uri": b64,
        }

    def apply_brand_styling(
        self, image_path: str, brand_id: str, output_path: str
    ) -> str:
        """Apply brand colours, overlays, and filters.

        Parameters
        ----------
        image_path:
            Source frame image.
        brand_id:
            Brand identifier.
        output_path:
            Destination path.

        Returns
        -------
        str
            Styled image path.
        """
        style = BRAND_THUMB_STYLES.get(brand_id, BRAND_THUMB_STYLES["human_success_guru"])
        overlay_color = style["overlay_color"]
        opacity = style["overlay_opacity"]

        # Apply colour overlay + slight blur for depth
        cmd = [
            "ffmpeg", "-y", "-i", image_path,
            "-vf", (
                f"scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:force_original_aspect_ratio=decrease,"
                f"pad={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:(ow-iw)/2:(oh-ih)/2,"
                f"colorbalance=rs=0.05:gs=0.02:bs=0.02,"
                f"eq=brightness=-{opacity * 0.1:.2f}:contrast=1.1"
            ),
            "-frames:v", "1",
            "-q:v", "2",
            output_path,
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=30)
            return output_path
        except Exception as exc:
            logger.warning("Brand styling failed: %s", exc)
            # Copy source as fallback
            import shutil
            shutil.copy2(image_path, output_path)
            return output_path

    def embed_text_overlay(
        self, image_path: str, text: str, brand_id: str
    ) -> str:
        """Add a readable text overlay (hook phrase) to the thumbnail.

        Parameters
        ----------
        image_path:
            Source image.
        text:
            Text to overlay.
        brand_id:
            Brand identifier (for styling).

        Returns
        -------
        str
            Image path with text overlay.
        """
        style = BRAND_THUMB_STYLES.get(brand_id, BRAND_THUMB_STYLES["human_success_guru"])
        text_color = style["text_color"]
        font_size = style["font_size"]

        # Escape single quotes in text for FFmpeg
        safe_text = text.replace("'", "'\\''").replace(":", "\\:")

        output = image_path.replace(".jpg", "_text.jpg")
        cmd = [
            "ffmpeg", "-y", "-i", image_path,
            "-vf", (
                f"drawtext=text='{safe_text}':"
                f"fontcolor={text_color}:fontsize={font_size}:"
                f"x=(w-text_w)/2:y=h*0.4:"
                f"borderw=3:bordercolor=black"
            ),
            "-frames:v", "1",
            "-q:v", "2",
            output,
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=30)
            return output
        except Exception as exc:
            logger.warning("Text overlay failed: %s", exc)
            return image_path

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _extract_key_frame(self, video_path: str, brand_id: str) -> str:
        """Extract a visually appealing frame from the video.

        Parameters
        ----------
        video_path:
            Source video file.
        brand_id:
            Brand identifier.

        Returns
        -------
        str
            Path to the extracted frame image.
        """
        out_dir = self.media_root / "temp" / brand_id
        out_dir.mkdir(parents=True, exist_ok=True)
        frame_path = str(out_dir / "key_frame.jpg")

        # Try extracting at 25% into the video (usually a good establishing shot)
        try:
            duration = self._get_video_duration(video_path)
            seek_time = max(duration * 0.25, 1.0)
            cmd = [
                "ffmpeg", "-y",
                "-ss", str(seek_time),
                "-i", video_path,
                "-frames:v", "1",
                "-q:v", "2",
                frame_path,
            ]
            subprocess.run(cmd, check=True, capture_output=True, timeout=15)
            return frame_path
        except Exception as exc:
            logger.warning("Key frame extraction failed: %s — using first frame", exc)
            # Fallback: extract first frame
            try:
                cmd = [
                    "ffmpeg", "-y", "-i", video_path,
                    "-frames:v", "1", "-q:v", "2",
                    frame_path,
                ]
                subprocess.run(cmd, check=True, capture_output=True, timeout=15)
                return frame_path
            except Exception:
                # Generate a solid colour frame as last resort
                return self._generate_solid_frame(brand_id, frame_path)

    def _generate_solid_frame(self, brand_id: str, output: str) -> str:
        """Generate a solid-colour branded frame as a last-resort fallback.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        output:
            Output path.

        Returns
        -------
        str
            Generated image path.
        """
        style = BRAND_THUMB_STYLES.get(brand_id, {})
        color = style.get("overlay_color", "#1a1a2e")

        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", f"color=c={color}:s={OUTPUT_WIDTH}x{OUTPUT_HEIGHT}:d=1:r=1",
            "-frames:v", "1",
            "-q:v", "2",
            output,
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=10)
        except Exception:
            Path(output).touch()
        return output

    @staticmethod
    def _get_video_duration(video_path: str) -> float:
        """Get video duration via ffprobe.

        Parameters
        ----------
        video_path:
            Path to video file.

        Returns
        -------
        float
            Duration in seconds.
        """
        try:
            result = subprocess.run(
                [
                    "ffprobe", "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "json", video_path,
                ],
                capture_output=True, text=True, timeout=10,
            )
            data = json.loads(result.stdout)
            return float(data.get("format", {}).get("duration", 5.0))
        except Exception:
            return 5.0

    @staticmethod
    def _extract_hook(script_text: str) -> str:
        """Extract the hook (first sentence) from the script.

        Parameters
        ----------
        script_text:
            Full script body.

        Returns
        -------
        str
            Hook sentence, truncated to 60 chars max for overlay.
        """
        import re
        sentences = re.split(r"[.!?]+", script_text.strip())
        hook = sentences[0].strip() if sentences else ""
        if len(hook) > 60:
            hook = hook[:57] + "..."
        return hook

    def _score_thumbnail(self, image_path: str) -> float:
        """Score a thumbnail for visual quality (0.0–1.0).

        Parameters
        ----------
        image_path:
            Path to thumbnail image.

        Returns
        -------
        float
            Quality score.
        """
        if not os.path.exists(image_path):
            return 0.0

        score = 0.5  # Base

        # File size check (larger JPEG = more detail)
        size_kb = os.path.getsize(image_path) / 1024
        if size_kb > 100:
            score += 0.2
        elif size_kb > 30:
            score += 0.1

        # Resolution check
        try:
            result = subprocess.run(
                [
                    "ffprobe", "-v", "error",
                    "-select_streams", "v:0",
                    "-show_entries", "stream=width,height",
                    "-of", "json", image_path,
                ],
                capture_output=True, text=True, timeout=10,
            )
            data = json.loads(result.stdout)
            streams = data.get("streams", [{}])
            if streams:
                w = streams[0].get("width", 0)
                h = streams[0].get("height", 0)
                if w >= 1080 and h >= 1920:
                    score += 0.2
                elif w >= 720:
                    score += 0.1
        except Exception:
            pass

        return min(round(score, 2), 1.0)

    @staticmethod
    def _to_base64_data_uri(image_path: str) -> str:
        """Convert an image to a base64 data URI for embedding.

        Parameters
        ----------
        image_path:
            Path to the image file.

        Returns
        -------
        str
            Base64 data URI string, or empty string on failure.
        """
        try:
            with open(image_path, "rb") as f:
                data = f.read()
            b64 = base64.b64encode(data).decode("utf-8")
            ext = Path(image_path).suffix.lower()
            mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png"}.get(
                ext.lstrip("."), "jpeg"
            )
            return f"data:image/{mime};base64,{b64}"
        except Exception as exc:
            logger.warning("Base64 encoding failed: %s", exc)
            return ""
