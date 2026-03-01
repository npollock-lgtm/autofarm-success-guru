"""
Trending Audio Tracker — discovers trending TikTok sounds and adds them
as low-volume background overlays to boost algorithm discoverability.

The overlay is applied during video assembly via FFmpeg — the trending
audio is mixed at ~5 % volume under the main voiceover + music.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("autofarm.marketing.trending_audio")

# Overlay volume relative to main audio (0.0 – 1.0)
TRENDING_AUDIO_VOLUME = 0.05

# Cache duration for trending sounds list (hours)
CACHE_TTL_HOURS = 6

# Max trending sounds to track at once
MAX_TRACKED_SOUNDS = 20

# Where to store downloaded audio clips
AUDIO_CACHE_DIR = Path("data/audio_cache/trending")


class TrendingAudioTracker:
    """Discover and apply trending audio overlays.

    Parameters
    ----------
    db:
        Database helper instance.
    rate_limiter:
        ``RateLimitManager`` for API compliance.
    credential_manager:
        ``CredentialManager`` for TikTok token access.
    ip_router:
        ``BrandIPRouter`` for session management.
    """

    def __init__(
        self,
        db: Any,
        rate_limiter: Any,
        credential_manager: Optional[Any] = None,
        ip_router: Optional[Any] = None,
    ) -> None:
        self.db = db
        self.rate_limiter = rate_limiter
        self.credential_manager = credential_manager
        self.ip_router = ip_router
        self._cache: Dict[str, Any] = {}
        self._cache_time: Optional[datetime] = None

    # ------------------------------------------------------------------
    # Discover trending sounds
    # ------------------------------------------------------------------

    async def discover_trending_sounds(
        self, brand_id: str = "human_success_guru"
    ) -> List[Dict[str, Any]]:
        """Fetch current trending sounds from TikTok.

        Parameters
        ----------
        brand_id:
            Brand whose credentials to use for the API call.

        Returns
        -------
        List[Dict[str, Any]]
            ``[{sound_id, title, artist, usage_count, audio_url}]``

        Side Effects
        ------------
        Caches results for ``CACHE_TTL_HOURS``.
        """
        # Check cache
        now = datetime.now(timezone.utc)
        if (
            self._cache_time
            and (now - self._cache_time).total_seconds() < CACHE_TTL_HOURS * 3600
            and self._cache.get("sounds")
        ):
            return self._cache["sounds"]

        sounds = await self._fetch_from_tiktok(brand_id)

        if sounds:
            self._cache["sounds"] = sounds[:MAX_TRACKED_SOUNDS]
            self._cache_time = now
            logger.info("Discovered %d trending sounds", len(sounds))

        return sounds[:MAX_TRACKED_SOUNDS]

    async def _fetch_from_tiktok(
        self, brand_id: str
    ) -> List[Dict[str, Any]]:
        """Call TikTok API for trending sounds.

        Parameters
        ----------
        brand_id:
            Brand for credentials.

        Returns
        -------
        List[Dict[str, Any]]
            Trending sound entries.
        """
        import aiohttp

        if not self.credential_manager or not self.ip_router:
            logger.warning("No credentials/router for trending audio")
            return []

        creds = await self.credential_manager.get_credentials(brand_id, "tiktok")
        if not creds or not creds.get("access_token"):
            return []

        allowed = await self.rate_limiter.check_and_increment(
            brand_id, "tiktok", "trending_sounds", units=1
        )
        if not allowed:
            return []

        session = await self.ip_router.get_session(brand_id, "tiktok")
        headers = {"Authorization": f"Bearer {creds['access_token']}"}
        url = "https://open.tiktokapis.com/v2/research/music/trending/"

        try:
            async with session.get(
                url, headers=headers,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status != 200:
                    logger.warning("TikTok trending API returned %d", resp.status)
                    return []
                data = await resp.json()

            sounds = []
            for item in data.get("data", {}).get("music_list", []):
                sounds.append({
                    "sound_id": item.get("id", ""),
                    "title": item.get("title", "Unknown"),
                    "artist": item.get("author", "Unknown"),
                    "usage_count": item.get("video_count", 0),
                    "audio_url": item.get("play_url", ""),
                    "duration_seconds": item.get("duration", 0),
                })
            return sounds

        except Exception as exc:
            logger.error("TikTok trending sounds error: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Download audio clip
    # ------------------------------------------------------------------

    async def download_trending_audio(
        self, sound: Dict[str, Any]
    ) -> Optional[Path]:
        """Download a trending audio clip for overlay use.

        Parameters
        ----------
        sound:
            Sound dict with ``sound_id`` and ``audio_url``.

        Returns
        -------
        Optional[Path]
            Path to downloaded audio file, or ``None``.
        """
        import aiohttp

        audio_url = sound.get("audio_url")
        if not audio_url:
            return None

        AUDIO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        sound_id = sound.get("sound_id", "unknown")
        output_path = AUDIO_CACHE_DIR / f"trending_{sound_id}.mp3"

        if output_path.exists():
            return output_path

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    audio_url,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.read()

            output_path.write_bytes(data)
            logger.info("Downloaded trending audio: %s", output_path.name)
            return output_path

        except Exception as exc:
            logger.error("Failed to download trending audio: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Apply overlay via FFmpeg
    # ------------------------------------------------------------------

    async def apply_trending_overlay(
        self,
        video_path: str,
        trending_audio_path: str,
        output_path: Optional[str] = None,
        volume: float = TRENDING_AUDIO_VOLUME,
    ) -> Optional[str]:
        """Mix a trending audio clip as a low-volume overlay on a video.

        Parameters
        ----------
        video_path:
            Path to the input video.
        trending_audio_path:
            Path to the trending audio clip.
        output_path:
            Output path. Defaults to ``{video}_trending.mp4``.
        volume:
            Overlay volume (0.0 – 1.0).

        Returns
        -------
        Optional[str]
            Path to the output video, or ``None`` on failure.
        """
        if output_path is None:
            base = Path(video_path)
            output_path = str(base.with_suffix("")) + "_trending.mp4"

        # FFmpeg command: mix trending audio at low volume with existing audio
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", trending_audio_path,
            "-filter_complex",
            (
                f"[1:a]volume={volume},aloop=loop=-1:size=2e+09[trending];"
                f"[0:a][trending]amix=inputs=2:duration=first:"
                f"dropout_transition=2[aout]"
            ),
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "128k",
            "-shortest",
            output_path,
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                logger.error("FFmpeg trending overlay failed: %s", result.stderr[:500])
                return None

            logger.info("Applied trending overlay to %s", output_path)
            return output_path

        except subprocess.TimeoutExpired:
            logger.error("FFmpeg trending overlay timed out")
            return None
        except Exception as exc:
            logger.error("Trending overlay error: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Select best trending sound for a brand
    # ------------------------------------------------------------------

    async def select_trending_sound(
        self,
        brand_id: str,
        genre_hints: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Pick the most relevant trending sound for a brand.

        Prefers sounds with high usage counts.  If ``genre_hints`` are
        provided, filters by title/artist matching.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        genre_hints:
            Optional keywords to match against title/artist.

        Returns
        -------
        Optional[Dict[str, Any]]
            Selected sound dict or ``None``.
        """
        sounds = await self.discover_trending_sounds(brand_id)
        if not sounds:
            return None

        if genre_hints:
            # Filter by keyword match
            filtered = []
            for s in sounds:
                title_lower = (s.get("title", "") or "").lower()
                artist_lower = (s.get("artist", "") or "").lower()
                for hint in genre_hints:
                    if hint.lower() in title_lower or hint.lower() in artist_lower:
                        filtered.append(s)
                        break
            if filtered:
                sounds = filtered

        # Sort by usage count and pick top
        sounds.sort(key=lambda s: s.get("usage_count", 0), reverse=True)
        return sounds[0] if sounds else None

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def cleanup_old_audio(self, max_age_days: int = 7) -> int:
        """Remove cached trending audio files older than max_age_days.

        Parameters
        ----------
        max_age_days:
            Maximum age in days before deletion.

        Returns
        -------
        int
            Number of files removed.
        """
        if not AUDIO_CACHE_DIR.exists():
            return 0

        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        removed = 0

        for f in AUDIO_CACHE_DIR.glob("trending_*.mp3"):
            try:
                mtime = datetime.fromtimestamp(
                    f.stat().st_mtime, tz=timezone.utc
                )
                if mtime < cutoff:
                    f.unlink()
                    removed += 1
            except OSError as exc:
                logger.warning("Failed to remove %s: %s", f.name, exc)

        if removed:
            logger.info("Cleaned up %d old trending audio files", removed)
        return removed
