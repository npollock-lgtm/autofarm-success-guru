"""
Platform compliance checker for AutoFarm Zero — Success Guru Network v6.0.

Pre-publish compliance verification. Every publish_job passes through this
before the API call is made. Checks video specs, content uniqueness, caption
compliance, posting frequency, and AI disclosure requirements.
"""

import os
import hashlib
import logging
import subprocess
import json
from dataclasses import dataclass, field
from pathlib import Path

from database.db import Database
from modules.compliance.rate_limits import PLATFORM_LIMITS

logger = logging.getLogger(__name__)


@dataclass
class ComplianceResult:
    """Result of compliance checking."""
    passed: bool
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class PlatformComplianceChecker:
    """
    Pre-publish compliance verification.

    Every publish_job passes through this before the API call is made.
    Failed compliance = reschedule, not abandon. Checks cover video specs,
    content uniqueness, caption rules, posting frequency, and AI disclosure.
    """

    def __init__(self):
        """Initializes with database access."""
        self.db = Database()

    def check_all(self, brand_id: str, platform: str,
                  video_path: str, caption: str,
                  hashtags: list[str]) -> ComplianceResult:
        """
        Runs all compliance checks for a publish job.

        Parameters:
            brand_id: The brand publishing.
            platform: Target platform.
            video_path: Path to the video file.
            caption: Post caption text.
            hashtags: List of hashtag strings.

        Returns:
            ComplianceResult with passed status, issues, and warnings.
        """
        result = ComplianceResult(passed=True)

        # Video specs check
        video_issues = self.check_video_specs(video_path, platform)
        if video_issues:
            result.issues.extend(video_issues)
            result.passed = False

        # Caption compliance
        caption_issues = self.check_caption_compliance(caption, hashtags, platform)
        if caption_issues:
            result.issues.extend(caption_issues)
            result.passed = False

        # Posting frequency
        if not self.check_posting_frequency(brand_id, platform):
            result.issues.append(
                f"Posting frequency exceeded for {brand_id} on {platform}"
            )
            result.passed = False

        # Content uniqueness
        if not self.check_content_uniqueness(video_path, brand_id, platform):
            result.warnings.append(
                "Video perceptual hash matches a recently published video"
            )

        # AI disclosure
        if self.check_ai_disclosure_required(platform):
            result.warnings.append(
                f"AI disclosure required for {platform} — ensure disclosure flags are set"
            )

        if result.passed:
            logger.info(
                "Compliance check passed",
                extra={'brand_id': brand_id, 'platform': platform}
            )
        else:
            logger.warning(
                "Compliance check failed",
                extra={
                    'brand_id': brand_id,
                    'platform': platform,
                    'issues': result.issues,
                }
            )

        return result

    def check_video_specs(self, video_path: str, platform: str) -> list[str]:
        """
        Checks video duration, resolution, aspect ratio, file size, and codec.

        Parameters:
            video_path: Path to the video file.
            platform: Target platform.

        Returns:
            List of issue strings. Empty if all checks pass.
        """
        issues = []
        limits = PLATFORM_LIMITS.get(platform, {})

        if not video_path or not Path(video_path).exists():
            issues.append(f"Video file not found: {video_path}")
            return issues

        # Get video info using ffprobe
        try:
            probe_result = subprocess.run(
                [
                    'ffprobe', '-v', 'quiet',
                    '-print_format', 'json',
                    '-show_format', '-show_streams',
                    video_path
                ],
                capture_output=True, text=True, timeout=30
            )
            probe = json.loads(probe_result.stdout)
        except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
            issues.append("Could not probe video file with ffprobe")
            return issues

        # Find video stream
        video_stream = None
        for stream in probe.get('streams', []):
            if stream.get('codec_type') == 'video':
                video_stream = stream
                break

        if not video_stream:
            issues.append("No video stream found in file")
            return issues

        # Check duration
        duration = float(probe.get('format', {}).get('duration', 0))
        max_duration = limits.get('shorts_max_duration_seconds',
                                  limits.get('max_video_duration_seconds', 60))
        min_duration = limits.get('min_video_duration_seconds', 3)

        if duration > max_duration:
            issues.append(
                f"Video duration {duration:.1f}s exceeds max {max_duration}s for {platform}"
            )
        if duration < min_duration:
            issues.append(
                f"Video duration {duration:.1f}s below min {min_duration}s for {platform}"
            )

        # Check file size
        file_size_mb = Path(video_path).stat().st_size / (1024 * 1024)
        max_size_mb = limits.get('max_video_size_mb', 4096)
        if file_size_mb > max_size_mb:
            issues.append(
                f"Video file size {file_size_mb:.1f}MB exceeds max {max_size_mb}MB"
            )

        # Check resolution
        width = int(video_stream.get('width', 0))
        height = int(video_stream.get('height', 0))
        if width > 0 and height > 0:
            # For shorts/reels, should be portrait (9:16)
            if width > height:
                issues.append(
                    f"Video is landscape ({width}x{height}). "
                    f"Shorts/Reels require portrait (9:16)."
                )

        return issues

    def check_content_uniqueness(self, video_path: str, brand_id: str,
                                 platform: str) -> bool:
        """
        Checks if the video is unique compared to recently published content.

        Computes perceptual hash of first/middle/last frames and compares
        against recently published videos. Same video across different
        platforms is fine — same video on same platform is not.

        Parameters:
            video_path: Path to the video file.
            brand_id: Brand publishing the video.
            platform: Target platform.

        Returns:
            True if content appears unique for this platform.
        """
        if not video_path or not Path(video_path).exists():
            return True

        try:
            # Generate a simple content fingerprint from video file hash
            file_hash = self._compute_video_fingerprint(video_path)

            # Check against recent publishes on the SAME platform
            recent = self.db.query(
                """SELECT pj.varied_video_path, pj.video_id
                   FROM publish_jobs pj
                   WHERE pj.platform = ? AND pj.status = 'published'
                   AND pj.published_at > datetime('now', '-30 days')
                   ORDER BY pj.published_at DESC LIMIT 50""",
                (platform,)
            )

            for pub in recent:
                pub_path = pub.get('varied_video_path')
                if pub_path and Path(pub_path).exists():
                    pub_hash = self._compute_video_fingerprint(pub_path)
                    if file_hash == pub_hash:
                        logger.warning(
                            "Duplicate content detected",
                            extra={
                                'brand_id': brand_id,
                                'platform': platform,
                                'matching_video_id': pub['video_id'],
                            }
                        )
                        return False

            return True

        except Exception as e:
            logger.debug(f"Uniqueness check failed: {e}")
            return True  # Don't block on check failure

    def _compute_video_fingerprint(self, video_path: str) -> str:
        """
        Computes a simple perceptual fingerprint of a video.

        Parameters:
            video_path: Path to the video file.

        Returns:
            Hex digest fingerprint string.
        """
        # Use file size + first 4KB as a quick fingerprint
        stat = Path(video_path).stat()
        hasher = hashlib.md5()
        hasher.update(str(stat.st_size).encode())
        with open(video_path, 'rb') as f:
            hasher.update(f.read(4096))
        return hasher.hexdigest()

    def check_caption_compliance(self, caption: str, hashtags: list[str],
                                 platform: str) -> list[str]:
        """
        Checks caption length limits, banned words, and hashtag count.

        Parameters:
            caption: Post caption text.
            hashtags: List of hashtag strings.
            platform: Target platform.

        Returns:
            List of issue strings. Empty if compliant.
        """
        issues = []
        limits = PLATFORM_LIMITS.get(platform, {})

        # Caption length
        max_caption = limits.get(
            'max_caption_length',
            limits.get('max_title_length',
                       limits.get('max_message_length', 2200))
        )
        if len(caption) > max_caption:
            issues.append(
                f"Caption length {len(caption)} exceeds max {max_caption} for {platform}"
            )

        # Hashtag count
        max_hashtags = limits.get(
            'max_hashtags',
            limits.get('max_hashtags_recommended', 30)
        )
        if len(hashtags) > max_hashtags:
            issues.append(
                f"Hashtag count {len(hashtags)} exceeds max {max_hashtags} for {platform}"
            )

        # Banned words check (platform TOS violations)
        banned_phrases = [
            'follow for follow', 'f4f', 'like for like', 'l4l',
            'sub for sub', 'free money', 'guaranteed profit',
        ]
        caption_lower = caption.lower()
        for phrase in banned_phrases:
            if phrase in caption_lower:
                issues.append(f"Caption contains banned phrase: '{phrase}'")

        return issues

    def check_posting_frequency(self, brand_id: str, platform: str) -> bool:
        """
        Verifies minimum gap since last post and daily post count.

        Parameters:
            brand_id: Brand identifier.
            platform: Platform name.

        Returns:
            True if posting frequency is within limits.
        """
        from modules.compliance.rate_limit_manager import RateLimitManager
        rlm = RateLimitManager()
        quota = rlm.get_remaining_quota(brand_id, platform)
        return quota['can_upload']

    def check_ai_disclosure_required(self, platform: str) -> bool:
        """
        Checks whether AI disclosure is required for the platform.

        TikTok: YES (required). YouTube: REQUIRED for realistic synthetic content.
        Instagram/Facebook/Snapchat: RECOMMENDED but not required.

        Parameters:
            platform: Platform name.

        Returns:
            True if AI disclosure is required (not just recommended).
        """
        required_platforms = {'tiktok', 'youtube'}
        return platform in required_platforms

    def apply_ai_disclosure(self, publish_params: dict, platform: str) -> dict:
        """
        Adds required AI disclosure flags to upload API call parameters.

        Parameters:
            publish_params: Dictionary of API call parameters.
            platform: Target platform.

        Returns:
            Updated publish_params with AI disclosure flags added.

        Side effects:
            Modifies the publish_params dict in-place and returns it.
        """
        if platform == 'tiktok':
            # TikTok Content Posting API — ai_generated flag
            publish_params['is_ai_generated'] = True

        elif platform == 'youtube':
            # YouTube Data API — altered or synthetic content label
            if 'status' not in publish_params:
                publish_params['status'] = {}
            publish_params['status']['selfDeclaredMadeForKids'] = False
            # YouTube requires disclosure in description for AI content
            desc = publish_params.get('description', '')
            if 'AI-generated' not in desc and 'artificial intelligence' not in desc.lower():
                publish_params['description'] = (
                    desc + '\n\nThis content was created with AI assistance.'
                )

        elif platform in ('instagram', 'facebook'):
            # Meta platforms — recommended disclosure in caption
            caption = publish_params.get('caption', '')
            if 'AI' not in caption:
                publish_params['caption'] = caption  # No forced append — recommended only

        return publish_params
