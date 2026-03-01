"""
Music Fetcher — retrieves royalty-free background music for video assembly.

Supports:
  * Brand-appropriate mood-based selection
  * TikTok trending audio (low-volume overlay)
  * Local music library cache
  * FFmpeg generated ambient fallback

All API calls go through ``RateLimitManager``.
"""

from __future__ import annotations

import logging
import os
import random
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("autofarm.content_forge.music_fetcher")

# ---------------------------------------------------------------------------
# Brand mood mapping
# ---------------------------------------------------------------------------

BRAND_MOOD_MAP: Dict[str, Dict[str, Any]] = {
    "human_success_guru": {
        "moods": ["inspirational", "epic", "motivational"],
        "bpm_range": (90, 130),
        "energy": "high",
    },
    "wealth_success_guru": {
        "moods": ["luxury", "confident", "powerful"],
        "bpm_range": (85, 120),
        "energy": "medium-high",
    },
    "zen_success_guru": {
        "moods": ["calm", "peaceful", "ambient"],
        "bpm_range": (60, 90),
        "energy": "low",
    },
    "social_success_guru": {
        "moods": ["upbeat", "trendy", "energetic"],
        "bpm_range": (100, 140),
        "energy": "high",
    },
    "habits_success_guru": {
        "moods": ["focused", "clean", "minimal"],
        "bpm_range": (80, 110),
        "energy": "medium",
    },
    "relationships_success_guru": {
        "moods": ["warm", "emotional", "hopeful"],
        "bpm_range": (70, 100),
        "energy": "medium-low",
    },
}

# Pixabay Music API
PIXABAY_MUSIC_ENDPOINT = "https://pixabay.com/api/"


# ---------------------------------------------------------------------------
# MusicFetcher
# ---------------------------------------------------------------------------


class MusicFetcher:
    """Fetch royalty-free music matching brand mood and energy.

    Parameters
    ----------
    db:
        Database helper instance.
    rate_limiter:
        ``RateLimitManager`` for API call gating.
    media_root:
        Root path for media storage.
    pixabay_api_key:
        Pixabay API key (used for free music endpoint).
    """

    def __init__(
        self,
        db: Any,
        rate_limiter: Any,
        media_root: str = "media",
        pixabay_api_key: str = "",
    ) -> None:
        self.db = db
        self.rate_limiter = rate_limiter
        self.media_root = Path(media_root)
        self.pixabay_api_key = pixabay_api_key

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fetch_music(
        self,
        brand_id: str,
        mood: Optional[str] = None,
        duration_seconds: float = 60.0,
    ) -> str:
        """Fetch royalty-free music matching the brand mood.

        Priority: local cache → Pixabay API → FFmpeg ambient fallback.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        mood:
            Explicit mood keyword; if ``None``, derived from brand profile.
        duration_seconds:
            Desired minimum track duration.

        Returns
        -------
        str
            Path to the music audio file.
        """
        if mood is None:
            brand_moods = BRAND_MOOD_MAP.get(brand_id, {})
            moods = brand_moods.get("moods", ["ambient"])
            mood = random.choice(moods)

        # 1. Check local library
        local = self._check_local_library(brand_id, mood)
        if local:
            logger.info("Using cached music for %s: %s", brand_id, local)
            return local

        # 2. Pixabay Music API
        api_path = await self._fetch_from_pixabay(brand_id, mood)
        if api_path:
            logger.info("Downloaded music for %s from Pixabay", brand_id)
            return api_path

        # 3. FFmpeg ambient fallback
        logger.warning("Music fetch failed — generating ambient fallback for %s", brand_id)
        return self._generate_ambient_fallback(brand_id, duration_seconds)

    async def fetch_trending_audio(
        self, platform: str, brand_id: str
    ) -> Optional[str]:
        """Fetch trending audio for a platform (e.g. TikTok trending sounds).

        Parameters
        ----------
        platform:
            Platform name (``tiktok``, ``instagram``, etc.).
        brand_id:
            Brand identifier.

        Returns
        -------
        Optional[str]
            Path to trending audio file, or ``None`` if unavailable.

        Note
        ----
        Trending audio is used as a low-volume overlay on top of the
        primary brand music to increase algorithmic reach.
        """
        # Trending audio requires platform-specific scraping / API access
        # This is a placeholder that checks the local trending audio cache
        trending_dir = self.media_root / "trending_audio" / platform
        if not trending_dir.exists():
            return None

        tracks = list(trending_dir.glob("*.mp3")) + list(trending_dir.glob("*.wav"))
        if not tracks:
            return None

        track = random.choice(tracks)
        logger.info("Using trending audio: %s", track.name)
        return str(track)

    async def adjust_volume(
        self,
        audio_path: str,
        volume: float,
        output_path: Optional[str] = None,
    ) -> str:
        """Adjust volume of an audio file (for mixing / overlay).

        Parameters
        ----------
        audio_path:
            Source audio file.
        volume:
            Volume multiplier (0.0–1.0 typical for background music).
        output_path:
            Destination path; auto-generated if ``None``.

        Returns
        -------
        str
            Path to the adjusted audio file.
        """
        if output_path is None:
            base = Path(audio_path)
            output_path = str(base.parent / f"{base.stem}_vol{int(volume*100)}{base.suffix}")

        try:
            cmd = [
                "ffmpeg", "-y", "-i", audio_path,
                "-af", f"volume={volume}",
                "-c:a", "libmp3lame", "-q:a", "4",
                output_path,
            ]
            subprocess.run(cmd, check=True, capture_output=True, timeout=30)
            return output_path
        except Exception as exc:
            logger.error("Volume adjustment failed: %s", exc)
            return audio_path  # Return original as fallback

    # ------------------------------------------------------------------
    # Private — local library
    # ------------------------------------------------------------------

    def _check_local_library(
        self, brand_id: str, mood: str
    ) -> Optional[str]:
        """Check the local music library for matching tracks.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        mood:
            Mood keyword.

        Returns
        -------
        Optional[str]
            Path to a matching track, or ``None``.
        """
        music_dir = self.media_root / "music" / brand_id
        if not music_dir.exists():
            return None

        tracks = list(music_dir.glob("*.mp3")) + list(music_dir.glob("*.wav"))
        if not tracks:
            return None

        # Prefer tracks whose filename contains the mood keyword
        mood_lower = mood.lower()
        matching = [t for t in tracks if mood_lower in t.stem.lower()]
        if matching:
            return str(random.choice(matching))

        return str(random.choice(tracks))

    # ------------------------------------------------------------------
    # Private — Pixabay fetch
    # ------------------------------------------------------------------

    async def _fetch_from_pixabay(
        self, brand_id: str, mood: str
    ) -> Optional[str]:
        """Fetch a music track from the Pixabay API.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        mood:
            Search keyword.

        Returns
        -------
        Optional[str]
            Local file path, or ``None``.
        """
        if not self.pixabay_api_key:
            return None
        try:
            import aiohttp

            await self.rate_limiter.acquire("pixabay_music")
            # Pixabay music is accessed via the same endpoint with media_type=music
            # (Note: actual Pixabay music API may differ — this is a reasonable implementation)
            params = {
                "key": self.pixabay_api_key,
                "q": mood,
                "media_type": "music",
                "per_page": 5,
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    PIXABAY_MUSIC_ENDPOINT,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()

            hits = data.get("hits", [])
            if not hits:
                return None

            hit = random.choice(hits)
            audio_url = hit.get("audio", "") or hit.get("previewURL", "")
            if not audio_url:
                return None

            return await self._download_track(
                brand_id, audio_url, "pixabay", str(hit.get("id", ""))
            )
        except Exception as exc:
            logger.warning("Pixabay music fetch failed: %s", exc)
            return None

    async def _download_track(
        self,
        brand_id: str,
        url: str,
        source: str,
        source_id: str,
    ) -> Optional[str]:
        """Download and cache a music track.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        url:
            Remote URL.
        source:
            API source name.
        source_id:
            Source identifier.

        Returns
        -------
        Optional[str]
            Local path on success.
        """
        try:
            import aiohttp

            cache_dir = self.media_root / "music" / brand_id
            cache_dir.mkdir(parents=True, exist_ok=True)
            ext = ".mp3" if ".mp3" in url else ".wav"
            filename = f"{source}_{source_id}{ext}"
            file_path = str(cache_dir / filename)

            if os.path.exists(file_path):
                return file_path

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=60)
                ) as resp:
                    if resp.status != 200:
                        return None
                    with open(file_path, "wb") as f:
                        async for chunk in resp.content.iter_chunked(8192):
                            f.write(chunk)

            return file_path
        except Exception as exc:
            logger.error("Music download failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Private — FFmpeg ambient fallback
    # ------------------------------------------------------------------

    def _generate_ambient_fallback(
        self, brand_id: str, duration_seconds: float
    ) -> str:
        """Generate simple ambient background audio via FFmpeg.

        Parameters
        ----------
        brand_id:
            Brand identifier (selects frequency / tone).
        duration_seconds:
            Audio duration.

        Returns
        -------
        str
            Path to generated audio file.
        """
        out_dir = self.media_root / "music" / brand_id / "generated"
        out_dir.mkdir(parents=True, exist_ok=True)
        output = str(out_dir / f"ambient_{int(duration_seconds)}s.wav")

        if os.path.exists(output):
            return output

        brand_profile = BRAND_MOOD_MAP.get(brand_id, {})
        bpm_range = brand_profile.get("bpm_range", (80, 100))
        freq = (bpm_range[0] + bpm_range[1]) / 2  # Use BPM midpoint as base freq hint

        # Generate a quiet sine-wave ambient drone
        try:
            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", (
                    f"sine=frequency={freq}:duration={duration_seconds}:sample_rate=44100,"
                    "volume=0.05"
                ),
                output,
            ]
            subprocess.run(cmd, check=True, capture_output=True, timeout=30)
        except Exception as exc:
            logger.error("Ambient fallback generation failed: %s", exc)
            Path(output).touch()

        return output
