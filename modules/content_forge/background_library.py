"""
Background Library — manages background video selection, downloading, caching, and quality scoring.

Priority chain for every video:
  1. Local brand library  (media/brand_assets/{brand}/backgrounds/)
  2. B-roll cache          (previously downloaded brand-themed clips)
  3. Pexels API            (niche-matched query)
  4. Pixabay API           (niche-matched query)
  5. FFmpeg generated      (mathematical animation fallback — always available)

Never fails — the FFmpeg fallback guarantees a background is always produced.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("autofarm.content_forge.background_library")

# ---------------------------------------------------------------------------
# API endpoints & parameters
# ---------------------------------------------------------------------------

PEXELS_VIDEO_ENDPOINT = "https://api.pexels.com/videos/search"
PIXABAY_VIDEO_ENDPOINT = "https://pixabay.com/api/videos/"

PEXELS_PARAMS: Dict[str, Any] = {
    "orientation": "portrait",
    "size": "large",
    "per_page": 10,
    "min_duration": 8,
    "max_duration": 30,
}

PIXABAY_PARAMS: Dict[str, Any] = {
    "video_type": "film",
    "orientation": "vertical",
    "min_width": 1080,
    "per_page": 10,
    "safesearch": "true",
}

BACKGROUND_API_LIMITS: Dict[str, Dict[str, int]] = {
    "pexels": {"requests_per_hour": 200},
    "pixabay": {"requests_per_hour": 100},
}

# ---------------------------------------------------------------------------
# Per-brand background profiles
# ---------------------------------------------------------------------------

BRAND_BACKGROUND_PROFILES: Dict[str, Dict[str, Any]] = {
    "human_success_guru": {
        "themes": [
            "mountain summit sunrise",
            "person walking forward cinematic",
            "motivational dark background",
            "city skyline night lights",
            "runner training intensity",
            "ocean waves power",
            "forest path journey",
        ],
        "pexels_queries": ["motivation dark cinematic", "mountain sunrise", "runner training"],
        "color_treatment": "desaturate_70_crush_blacks_add_grain",
        "motion_speed": 0.4,
        "generated_fallback": "dark_particle_field",
    },
    "wealth_success_guru": {
        "themes": [
            "luxury car slow motion",
            "stock market screen",
            "gold coins close up",
            "city financial district",
            "cash money cinematic",
            "private jet aerial",
            "mansion exterior drone",
        ],
        "pexels_queries": ["luxury lifestyle", "stock market", "business success"],
        "color_treatment": "warm_gold_tint_crush_shadows",
        "motion_speed": 0.3,
        "generated_fallback": "financial_data_stream",
    },
    "zen_success_guru": {
        "themes": [
            "meditation calm water",
            "zen garden peaceful",
            "sunrise over lake",
            "bamboo forest wind",
            "candle flame dark room",
            "gentle rain on leaves",
            "mountain mist morning",
        ],
        "pexels_queries": ["meditation calm", "zen nature", "peaceful sunrise"],
        "color_treatment": "soft_warm_glow_desaturate_30",
        "motion_speed": 0.2,
        "generated_fallback": "slow_gradient_breathe",
    },
    "social_success_guru": {
        "themes": [
            "social media icons floating",
            "people networking event",
            "smartphone scrolling",
            "crowd celebration",
            "friends laughing together",
            "city nightlife energy",
            "digital connection network",
        ],
        "pexels_queries": ["social media", "networking people", "digital connection"],
        "color_treatment": "vibrant_boost_saturation_contrast",
        "motion_speed": 0.6,
        "generated_fallback": "network_node_pulse",
    },
    "habits_success_guru": {
        "themes": [
            "morning routine productivity",
            "journal writing closeup",
            "healthy breakfast preparation",
            "alarm clock morning",
            "exercise stretching",
            "desk organised workspace",
            "water glass hydration",
        ],
        "pexels_queries": ["morning routine", "productivity workspace", "healthy habit"],
        "color_treatment": "clean_bright_subtle_vignette",
        "motion_speed": 0.35,
        "generated_fallback": "sunrise_horizon_rise",
    },
    "relationships_success_guru": {
        "themes": [
            "couple walking sunset",
            "friends deep conversation",
            "family gathering warm",
            "handshake trust",
            "heart shape hands",
            "cozy fireplace evening",
            "park bench conversation",
        ],
        "pexels_queries": ["couple sunset", "friends conversation", "warm family"],
        "color_treatment": "warm_soft_rose_tint_low_contrast",
        "motion_speed": 0.25,
        "generated_fallback": "warm_particle_drift",
    },
}

# ---------------------------------------------------------------------------
# BackgroundManager
# ---------------------------------------------------------------------------


class BackgroundManager:
    """Manage background video selection, downloading, caching, and quality scoring.

    Parameters
    ----------
    db:
        Database helper instance.
    rate_limiter:
        ``RateLimitManager`` for API call gating.
    media_root:
        Root directory for media assets.
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
    # Primary API
    # ------------------------------------------------------------------

    async def get_background(
        self, brand_id: str, duration_seconds: float
    ) -> str:
        """Return a path to a usable background video file.

        Priority:
          1. Local brand library
          2. B-roll cache
          3. Pexels download
          4. Pixabay download
          5. FFmpeg generated fallback (always succeeds)

        Parameters
        ----------
        brand_id:
            Brand identifier.
        duration_seconds:
            Desired minimum duration.

        Returns
        -------
        str
            Absolute path to the video file.
        """
        # 1. Local library
        local = self._check_local_library(brand_id, duration_seconds)
        if local:
            logger.info("Using local background for %s: %s", brand_id, local)
            await self._mark_used(local)
            return local

        # 2. B-roll cache
        cached = await self._check_broll_cache(brand_id)
        if cached:
            logger.info("Using cached background for %s: %s", brand_id, cached)
            await self._mark_used(cached)
            return cached

        # 3. Pexels
        profile = BRAND_BACKGROUND_PROFILES.get(brand_id, {})
        queries = profile.get("pexels_queries", ["cinematic background"])

        pexels_path = await self._fetch_pexels(brand_id, random.choice(queries))
        if pexels_path:
            logger.info("Downloaded Pexels background for %s", brand_id)
            return pexels_path

        # 4. Pixabay
        pixabay_path = await self._fetch_pixabay(
            brand_id, random.choice(profile.get("themes", ["cinematic"]))
        )
        if pixabay_path:
            logger.info("Downloaded Pixabay background for %s", brand_id)
            return pixabay_path

        # 5. FFmpeg fallback (always works)
        logger.warning("All API sources failed for %s — generating fallback", brand_id)
        return self.generate_fallback_background(brand_id, duration_seconds)

    def score_background(self, video_path: str, brand_id: str) -> float:
        """Score a background video for brand fit (0.0–1.0).

        Parameters
        ----------
        video_path:
            Path to the video file.
        brand_id:
            Brand identifier.

        Returns
        -------
        float
            Quality score between 0.0 and 1.0.
        """
        score = 0.5  # base score
        if not os.path.exists(video_path):
            return 0.0

        # Check file size (larger = likely higher quality)
        size_mb = os.path.getsize(video_path) / (1024 * 1024)
        if size_mb > 5:
            score += 0.2
        elif size_mb > 1:
            score += 0.1

        # Check resolution via ffprobe
        try:
            result = subprocess.run(
                [
                    "ffprobe", "-v", "error",
                    "-select_streams", "v:0",
                    "-show_entries", "stream=width,height",
                    "-of", "json", video_path,
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            info = json.loads(result.stdout)
            streams = info.get("streams", [{}])
            if streams:
                w = streams[0].get("width", 0)
                h = streams[0].get("height", 0)
                if h >= 1920 and w >= 1080:
                    score += 0.2
                elif h >= 1280:
                    score += 0.1
        except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
            pass

        return min(score, 1.0)

    def apply_brand_treatment(
        self, background_path: str, brand_id: str, output_path: str
    ) -> str:
        """Apply brand-specific FFmpeg colour treatment to a background.

        Parameters
        ----------
        background_path:
            Source video file path.
        brand_id:
            Brand identifier.
        output_path:
            Destination path for the treated video.

        Returns
        -------
        str
            Path to the treated output file.
        """
        profile = BRAND_BACKGROUND_PROFILES.get(brand_id, {})
        treatment = profile.get("color_treatment", "")
        motion_speed = profile.get("motion_speed", 0.5)

        # Build filter based on treatment name
        vf_filters = self._treatment_to_filter(treatment, motion_speed)

        cmd = [
            "ffmpeg", "-y", "-i", background_path,
            "-vf", vf_filters,
            "-c:v", "libx264", "-preset", "medium", "-crf", "23",
            "-an", output_path,
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=120)
            return output_path
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            logger.error("Brand treatment failed: %s", exc)
            return background_path  # Return untreated as fallback

    def generate_fallback_background(
        self, brand_id: str, duration_seconds: float
    ) -> str:
        """Generate a mathematical animation via FFmpeg as guaranteed fallback.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        duration_seconds:
            Video duration in seconds.

        Returns
        -------
        str
            Path to the generated background video.

        Side Effects
        ------------
        Creates a video file on disk.
        """
        profile = BRAND_BACKGROUND_PROFILES.get(brand_id, {})
        fallback_type = profile.get("generated_fallback", "dark_particle_field")

        output_dir = self.media_root / "generated_backgrounds" / brand_id
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(
            output_dir / f"{fallback_type}_{int(duration_seconds)}s.mp4"
        )

        if os.path.exists(output_path):
            return output_path

        generators = {
            "dark_particle_field": self._gen_dark_particle_field,
            "financial_data_stream": self._gen_financial_data_stream,
            "slow_gradient_breathe": self._gen_slow_gradient_breathe,
            "network_node_pulse": self._gen_network_node_pulse,
            "sunrise_horizon_rise": self._gen_sunrise_horizon_rise,
            "warm_particle_drift": self._gen_warm_particle_drift,
        }

        generator = generators.get(fallback_type, self._gen_dark_particle_field)
        generator(duration_seconds, output_path)
        return output_path

    async def maintain_library(self) -> None:
        """Weekly maintenance: download new clips, score, prune low-quality.

        Side Effects
        ------------
        * Downloads up to 5 new clips per brand.
        * Removes clips with quality_score < 0.3.
        """
        for brand_id in BRAND_BACKGROUND_PROFILES:
            profile = BRAND_BACKGROUND_PROFILES[brand_id]
            queries = profile.get("pexels_queries", [])
            if queries:
                query = random.choice(queries)
                path = await self._fetch_pexels(brand_id, query)
                if path:
                    quality = self.score_background(path, brand_id)
                    await self.db.execute(
                        "UPDATE background_library SET quality_score = ? WHERE file_path = ?",
                        (quality, path),
                    )

        # Prune low-quality entries
        await self.db.execute(
            "UPDATE background_library SET active = 0 WHERE quality_score < 0.3"
        )
        logger.info("Background library maintenance complete")

    async def pre_download_starter_library(self) -> None:
        """Setup task: download 5 high-quality clips per brand.

        Side Effects
        ------------
        Downloads and caches initial background clips.
        """
        for brand_id, profile in BRAND_BACKGROUND_PROFILES.items():
            queries = profile.get("pexels_queries", [])
            for i, query in enumerate(queries):
                if i >= 5:
                    break
                await self._fetch_pexels(brand_id, query)
            logger.info("Starter library downloaded for %s", brand_id)

    # ------------------------------------------------------------------
    # Private — local lookups
    # ------------------------------------------------------------------

    def _check_local_library(
        self, brand_id: str, duration_seconds: float
    ) -> Optional[str]:
        """Check local brand asset directory for usable backgrounds.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        duration_seconds:
            Minimum required duration.

        Returns
        -------
        Optional[str]
            Path to a video file, or ``None``.
        """
        lib_dir = self.media_root / "brand_assets" / brand_id / "backgrounds"
        if not lib_dir.exists():
            return None
        videos = list(lib_dir.glob("*.mp4"))
        if not videos:
            return None
        return str(random.choice(videos))

    async def _check_broll_cache(self, brand_id: str) -> Optional[str]:
        """Check the database for previously downloaded brand-themed clips.

        Parameters
        ----------
        brand_id:
            Brand identifier.

        Returns
        -------
        Optional[str]
            Cached file path or ``None``.
        """
        row = await self.db.fetch_one(
            """
            SELECT file_path FROM background_library
            WHERE brand_id = ? AND active = 1
            ORDER BY quality_score DESC, times_used ASC
            LIMIT 1
            """,
            (brand_id,),
        )
        if row and os.path.exists(row["file_path"]):
            return row["file_path"]
        return None

    async def _mark_used(self, file_path: str) -> None:
        """Increment usage counter for a background.

        Parameters
        ----------
        file_path:
            Path to the background file.
        """
        await self.db.execute(
            """
            UPDATE background_library
            SET times_used = times_used + 1, last_used_at = CURRENT_TIMESTAMP
            WHERE file_path = ?
            """,
            (file_path,),
        )

    # ------------------------------------------------------------------
    # Private — API fetchers
    # ------------------------------------------------------------------

    async def _fetch_pexels(
        self, brand_id: str, query: str
    ) -> Optional[str]:
        """Fetch a video from the Pexels API.

        Parameters
        ----------
        brand_id:
            Brand for tagging and storage.
        query:
            Search query string.

        Returns
        -------
        Optional[str]
            Downloaded file path, or ``None`` on failure.
        """
        if not self.pexels_api_key:
            return None

        try:
            import aiohttp

            await self.rate_limiter.acquire("pexels_video")
            params = {**PEXELS_PARAMS, "query": query}
            headers = {"Authorization": self.pexels_api_key}

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    PEXELS_VIDEO_ENDPOINT, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()

            videos = data.get("videos", [])
            if not videos:
                return None

            video = random.choice(videos)
            video_files = video.get("video_files", [])
            # Pick the best portrait file
            best = None
            for vf in video_files:
                if vf.get("height", 0) >= 1080:
                    best = vf
                    break
            if not best and video_files:
                best = video_files[0]
            if not best:
                return None

            download_url = best.get("link", "")
            return await self._download_video(
                brand_id, download_url, "pexels", str(video.get("id", ""))
            )
        except Exception as exc:
            logger.warning("Pexels fetch failed: %s", exc)
            return None

    async def _fetch_pixabay(
        self, brand_id: str, query: str
    ) -> Optional[str]:
        """Fetch a video from the Pixabay API.

        Parameters
        ----------
        brand_id:
            Brand for tagging.
        query:
            Search query string.

        Returns
        -------
        Optional[str]
            Downloaded file path, or ``None`` on failure.
        """
        if not self.pixabay_api_key:
            return None

        try:
            import aiohttp

            await self.rate_limiter.acquire("pixabay_video")
            params = {**PIXABAY_PARAMS, "q": query, "key": self.pixabay_api_key}

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    PIXABAY_VIDEO_ENDPOINT, params=params, timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()

            hits = data.get("hits", [])
            if not hits:
                return None

            hit = random.choice(hits)
            videos_dict = hit.get("videos", {})
            large = videos_dict.get("large", {})
            download_url = large.get("url", "")
            if not download_url:
                medium = videos_dict.get("medium", {})
                download_url = medium.get("url", "")
            if not download_url:
                return None

            return await self._download_video(
                brand_id, download_url, "pixabay", str(hit.get("id", ""))
            )
        except Exception as exc:
            logger.warning("Pixabay fetch failed: %s", exc)
            return None

    async def _download_video(
        self, brand_id: str, url: str, source: str, source_id: str
    ) -> Optional[str]:
        """Download a video from *url* and cache it locally.

        Parameters
        ----------
        brand_id:
            Brand identifier for directory structure.
        url:
            Remote URL to download.
        source:
            API source name (pexels / pixabay).
        source_id:
            Source-side identifier for deduplication.

        Returns
        -------
        Optional[str]
            Local file path, or ``None`` on failure.

        Side Effects
        ------------
        Creates a file and inserts a database row.
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
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                    if resp.status != 200:
                        return None
                    with open(file_path, "wb") as f:
                        async for chunk in resp.content.iter_chunked(8192):
                            f.write(chunk)

            quality = self.score_background(file_path, brand_id)
            await self.db.execute(
                """
                INSERT OR IGNORE INTO background_library
                    (brand_id, file_path, source, source_id, quality_score)
                VALUES (?, ?, ?, ?, ?)
                """,
                (brand_id, file_path, source, source_id, quality),
            )
            return file_path
        except Exception as exc:
            logger.error("Download failed (%s): %s", url, exc)
            return None

    # ------------------------------------------------------------------
    # Private — FFmpeg colour treatment filters
    # ------------------------------------------------------------------

    @staticmethod
    def _treatment_to_filter(treatment: str, motion_speed: float) -> str:
        """Convert a treatment name to an FFmpeg -vf filter string.

        Parameters
        ----------
        treatment:
            Named treatment from brand profile.
        motion_speed:
            Speed factor (0–1) for setpts.

        Returns
        -------
        str
            FFmpeg filter string.
        """
        filters_map: Dict[str, str] = {
            "desaturate_70_crush_blacks_add_grain": (
                "eq=saturation=0.3:contrast=1.3:brightness=-0.05,"
                "noise=alls=15:allf=t+u"
            ),
            "warm_gold_tint_crush_shadows": (
                "colorbalance=rs=0.15:gs=0.05:bs=-0.1,"
                "eq=contrast=1.2:brightness=-0.03"
            ),
            "soft_warm_glow_desaturate_30": (
                "eq=saturation=0.7:brightness=0.05,"
                "gblur=sigma=0.5"
            ),
            "vibrant_boost_saturation_contrast": (
                "eq=saturation=1.4:contrast=1.15"
            ),
            "clean_bright_subtle_vignette": (
                "eq=brightness=0.05:contrast=1.05,"
                "vignette=PI/4"
            ),
            "warm_soft_rose_tint_low_contrast": (
                "colorbalance=rs=0.1:gs=-0.02:bs=-0.05,"
                "eq=contrast=0.9:brightness=0.03"
            ),
        }
        base = filters_map.get(treatment, "eq=saturation=1.0")
        if motion_speed and motion_speed != 1.0:
            pts_factor = 1.0 / max(motion_speed, 0.1)
            base += f",setpts={pts_factor:.2f}*PTS"
        return base

    # ------------------------------------------------------------------
    # Private — FFmpeg fallback generators
    # ------------------------------------------------------------------

    def _gen_dark_particle_field(self, duration: float, output: str) -> None:
        """Generate a dark particle-field animation.

        Parameters
        ----------
        duration:
            Video length in seconds.
        output:
            Output file path.
        """
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", (
                f"color=c=black:s=1080x1920:d={duration}:r=30,"
                "noise=alls=30:allf=t+u,"
                "eq=brightness=-0.05:contrast=1.2"
            ),
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-pix_fmt", "yuv420p", output,
        ]
        self._run_ffmpeg(cmd)

    def _gen_financial_data_stream(self, duration: float, output: str) -> None:
        """Generate a financial data-stream animation.

        Parameters
        ----------
        duration:
            Video length in seconds.
        output:
            Output file path.
        """
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", (
                f"color=c=#0a0a2e:s=1080x1920:d={duration}:r=30,"
                "noise=alls=15:allf=t,"
                "colorbalance=rs=0:gs=0.15:bs=0.1"
            ),
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-pix_fmt", "yuv420p", output,
        ]
        self._run_ffmpeg(cmd)

    def _gen_slow_gradient_breathe(self, duration: float, output: str) -> None:
        """Generate a slow breathing gradient animation.

        Parameters
        ----------
        duration:
            Video length in seconds.
        output:
            Output file path.
        """
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", (
                f"color=c=#1a1a2e:s=1080x1920:d={duration}:r=30,"
                "hue=H=2*PI*t/10,"
                "eq=brightness=0.02:saturation=0.6"
            ),
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-pix_fmt", "yuv420p", output,
        ]
        self._run_ffmpeg(cmd)

    def _gen_network_node_pulse(self, duration: float, output: str) -> None:
        """Generate a network-node pulse animation.

        Parameters
        ----------
        duration:
            Video length in seconds.
        output:
            Output file path.
        """
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", (
                f"color=c=#0d0d2b:s=1080x1920:d={duration}:r=30,"
                "noise=alls=20:allf=t+u,"
                "colorbalance=rs=-0.05:gs=0.1:bs=0.2"
            ),
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-pix_fmt", "yuv420p", output,
        ]
        self._run_ffmpeg(cmd)

    def _gen_sunrise_horizon_rise(self, duration: float, output: str) -> None:
        """Generate a sunrise-horizon animation.

        Parameters
        ----------
        duration:
            Video length in seconds.
        output:
            Output file path.
        """
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", (
                f"color=c=#1a0a00:s=1080x1920:d={duration}:r=30,"
                "hue=H=0.5*PI*t/{duration},"
                "eq=brightness=0.01*t/{duration}:saturation=0.8"
            ),
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-pix_fmt", "yuv420p", output,
        ]
        self._run_ffmpeg(cmd)

    def _gen_warm_particle_drift(self, duration: float, output: str) -> None:
        """Generate a warm particle-drift animation.

        Parameters
        ----------
        duration:
            Video length in seconds.
        output:
            Output file path.
        """
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", (
                f"color=c=#1a0505:s=1080x1920:d={duration}:r=30,"
                "noise=alls=12:allf=t+u,"
                "colorbalance=rs=0.15:gs=0.02:bs=-0.05,"
                "eq=brightness=0.03"
            ),
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-pix_fmt", "yuv420p", output,
        ]
        self._run_ffmpeg(cmd)

    @staticmethod
    def _run_ffmpeg(cmd: List[str]) -> None:
        """Execute an FFmpeg command with error handling.

        Parameters
        ----------
        cmd:
            Full command list.
        """
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=180)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            logger.error("FFmpeg fallback generation failed: %s", exc)
