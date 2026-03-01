"""
B-Roll Fetcher — retrieves supplementary B-roll clips for video assembly.

Priority chain:
  1. Local cache  (``media/broll_cache/{brand}/``)
  2. Pexels API   (primary)
  3. Pixabay API  (fallback)
  4. FFmpeg generated placeholder (guaranteed)

All API calls go through ``RateLimitManager``.
"""

from __future__ import annotations

import logging
import os
import random
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("autofarm.content_forge.broll_fetcher")

# ---------------------------------------------------------------------------
# Default API settings
# ---------------------------------------------------------------------------

PEXELS_VIDEO_ENDPOINT = "https://api.pexels.com/videos/search"
PIXABAY_VIDEO_ENDPOINT = "https://pixabay.com/api/videos/"


# ---------------------------------------------------------------------------
# BRollFetcher
# ---------------------------------------------------------------------------


class BRollFetcher:
    """Fetch and cache B-roll video clips for content assembly.

    Parameters
    ----------
    db:
        Database helper instance.
    rate_limiter:
        ``RateLimitManager`` for API call gating.
    media_root:
        Root path for media storage.
    pexels_api_key:
        Pexels API key.
    pixabay_api_key:
        Pixabay API key.
    """

    def __init__(
        self,
        db: Any,
        rate_limiter: Any,
        media_root: str = "media",
        pexels_api_key: str = "",
        pixabay_api_key: str = "",
    ) -> None:
        self.db = db
        self.rate_limiter = rate_limiter
        self.media_root = Path(media_root)
        self.pexels_api_key = pexels_api_key
        self.pixabay_api_key = pixabay_api_key

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fetch_broll(
        self,
        brand_id: str,
        theme: str,
        duration_seconds: float = 10.0,
        count: int = 1,
    ) -> List[str]:
        """Fetch B-roll clip(s) matching *theme*.

        Priority: cache → Pexels → Pixabay → FFmpeg fallback.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        theme:
            Descriptive theme / search query.
        duration_seconds:
            Desired minimum clip duration.
        count:
            Number of clips to retrieve.

        Returns
        -------
        List[str]
            Paths to downloaded / cached video files.
        """
        results: List[str] = []

        # 1. Check cache
        cached = await self.get_from_cache(brand_id, theme, count)
        results.extend(cached)
        if len(results) >= count:
            return results[:count]

        remaining = count - len(results)

        # 2. Pexels
        for _ in range(remaining):
            path = await self._fetch_from_pexels(brand_id, theme)
            if path:
                results.append(path)
                if len(results) >= count:
                    return results[:count]

        remaining = count - len(results)

        # 3. Pixabay
        for _ in range(remaining):
            path = await self._fetch_from_pixabay(brand_id, theme)
            if path:
                results.append(path)
                if len(results) >= count:
                    return results[:count]

        remaining = count - len(results)

        # 4. Generate fallback
        for _ in range(remaining):
            path = self._generate_fallback(brand_id, duration_seconds)
            results.append(path)

        return results[:count]

    async def get_from_cache(
        self, brand_id: str, theme: str, limit: int = 1
    ) -> List[str]:
        """Check local cache for previously downloaded clips.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        theme:
            Theme query (used loosely for matching).
        limit:
            Maximum clips to return.

        Returns
        -------
        List[str]
            Cached file paths.
        """
        cache_dir = self.media_root / "broll_cache" / brand_id
        if not cache_dir.exists():
            return []

        # Simple file scan — prefer files whose name loosely matches theme
        all_clips = list(cache_dir.glob("*.mp4"))
        if not all_clips:
            return []

        # Sort by theme relevance (basic keyword match)
        theme_words = set(theme.lower().split())
        scored = []
        for clip in all_clips:
            name_words = set(clip.stem.lower().replace("_", " ").split())
            overlap = len(theme_words & name_words)
            scored.append((overlap, clip))
        scored.sort(key=lambda x: x[0], reverse=True)

        paths = [str(s[1]) for s in scored[:limit] if os.path.exists(str(s[1]))]
        if paths:
            logger.info("Found %d cached B-roll clips for %s", len(paths), brand_id)
        return paths

    async def download_and_cache(
        self, brand_id: str, url: str, source: str, source_id: str
    ) -> Optional[str]:
        """Download a clip from *url* and store in the B-roll cache.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        url:
            Remote video URL.
        source:
            API source name (``pexels`` / ``pixabay``).
        source_id:
            Source-side identifier.

        Returns
        -------
        Optional[str]
            Local path on success, ``None`` on failure.

        Side Effects
        ------------
        Creates a file in ``media/broll_cache/{brand_id}/``.
        """
        try:
            import aiohttp

            cache_dir = self.media_root / "broll_cache" / brand_id
            cache_dir.mkdir(parents=True, exist_ok=True)
            filename = f"{source}_{source_id}.mp4"
            file_path = str(cache_dir / filename)

            if os.path.exists(file_path):
                return file_path

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=120)
                ) as resp:
                    if resp.status != 200:
                        return None
                    with open(file_path, "wb") as f:
                        async for chunk in resp.content.iter_chunked(8192):
                            f.write(chunk)

            logger.info("Downloaded B-roll: %s → %s", source, file_path)
            return file_path
        except Exception as exc:
            logger.error("B-roll download failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Private — API fetchers
    # ------------------------------------------------------------------

    async def _fetch_from_pexels(
        self, brand_id: str, query: str
    ) -> Optional[str]:
        """Search and download a B-roll clip from Pexels.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        query:
            Search query.

        Returns
        -------
        Optional[str]
            Local file path, or ``None``.
        """
        if not self.pexels_api_key:
            return None
        try:
            import aiohttp

            await self.rate_limiter.acquire("pexels_video")
            params = {
                "query": query,
                "orientation": "portrait",
                "size": "medium",
                "per_page": 5,
            }
            headers = {"Authorization": self.pexels_api_key}

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    PEXELS_VIDEO_ENDPOINT,
                    params=params,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()

            videos = data.get("videos", [])
            if not videos:
                return None

            video = random.choice(videos)
            files = video.get("video_files", [])
            best = next((f for f in files if f.get("height", 0) >= 720), None)
            if not best and files:
                best = files[0]
            if not best:
                return None

            return await self.download_and_cache(
                brand_id, best["link"], "pexels", str(video.get("id", ""))
            )
        except Exception as exc:
            logger.warning("Pexels B-roll fetch failed: %s", exc)
            return None

    async def _fetch_from_pixabay(
        self, brand_id: str, query: str
    ) -> Optional[str]:
        """Search and download a B-roll clip from Pixabay.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        query:
            Search query.

        Returns
        -------
        Optional[str]
            Local file path, or ``None``.
        """
        if not self.pixabay_api_key:
            return None
        try:
            import aiohttp

            await self.rate_limiter.acquire("pixabay_video")
            params = {
                "q": query,
                "key": self.pixabay_api_key,
                "video_type": "film",
                "orientation": "vertical",
                "per_page": 5,
                "safesearch": "true",
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    PIXABAY_VIDEO_ENDPOINT,
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
            videos = hit.get("videos", {})
            medium = videos.get("medium", {})
            url = medium.get("url", "")
            if not url:
                large = videos.get("large", {})
                url = large.get("url", "")
            if not url:
                return None

            return await self.download_and_cache(
                brand_id, url, "pixabay", str(hit.get("id", ""))
            )
        except Exception as exc:
            logger.warning("Pixabay B-roll fetch failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Private — fallback generator
    # ------------------------------------------------------------------

    def _generate_fallback(
        self, brand_id: str, duration_seconds: float
    ) -> str:
        """Generate a simple B-roll placeholder via FFmpeg.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        duration_seconds:
            Clip duration.

        Returns
        -------
        str
            Path to generated clip.
        """
        out_dir = self.media_root / "broll_cache" / brand_id / "generated"
        out_dir.mkdir(parents=True, exist_ok=True)
        output = str(out_dir / f"fallback_{int(duration_seconds)}s.mp4")

        if os.path.exists(output):
            return output

        try:
            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", f"color=c=#111111:s=1080x1920:d={duration_seconds}:r=30,"
                      "noise=alls=8:allf=t+u",
                "-c:v", "libx264", "-preset", "fast", "-crf", "28",
                "-pix_fmt", "yuv420p", output,
            ]
            subprocess.run(cmd, check=True, capture_output=True, timeout=60)
        except Exception as exc:
            logger.error("B-roll fallback generation failed: %s", exc)
            # Create a minimal placeholder
            Path(output).touch()

        return output
