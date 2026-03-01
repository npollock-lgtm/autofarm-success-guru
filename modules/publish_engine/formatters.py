"""
Platform Formatters — format captions, descriptions, and hashtags for each platform.
"""

from __future__ import annotations

import re
import logging
from typing import Any, Dict, List, Tuple

logger = logging.getLogger("autofarm.publish_engine.formatters")


class CaptionFormatter:
    """Format captions and hashtags for each platform's requirements."""

    @staticmethod
    def format_for_tiktok(
        caption: str, hashtags: List[str]
    ) -> str:
        """Format a caption for TikTok.

        Parameters
        ----------
        caption:
            Generic caption text.
        hashtags:
            List of hashtag strings (without ``#``).

        Returns
        -------
        str
            TikTok-ready caption (≤ 2 200 chars).
        """
        tags = CaptionFormatter.sanitize_hashtags(hashtags, max_count=5)
        tag_str = " ".join(f"#{t}" for t in tags)
        combined = f"{caption}\n\n{tag_str}" if tags else caption
        return combined[:2200]

    @staticmethod
    def format_for_instagram(
        caption: str, hashtags: List[str]
    ) -> str:
        """Format a caption for Instagram Reels.

        Parameters
        ----------
        caption:
            Generic caption text.
        hashtags:
            Hashtag list.

        Returns
        -------
        str
            Instagram-ready caption (≤ 2 200 chars, ≤ 30 hashtags).
        """
        tags = CaptionFormatter.sanitize_hashtags(hashtags, max_count=30)
        tag_str = " ".join(f"#{t}" for t in tags)
        combined = f"{caption}\n\n{tag_str}" if tags else caption
        return combined[:2200]

    @staticmethod
    def format_for_facebook(
        caption: str, hashtags: List[str]
    ) -> str:
        """Format a caption for Facebook.

        Parameters
        ----------
        caption:
            Generic caption text.
        hashtags:
            Hashtag list.

        Returns
        -------
        str
            Facebook-ready caption (practical ≤ 2 000 chars).
        """
        tags = CaptionFormatter.sanitize_hashtags(hashtags, max_count=10)
        tag_str = " ".join(f"#{t}" for t in tags)
        combined = f"{caption}\n\n{tag_str}" if tags else caption
        return combined[:2000]

    @staticmethod
    def format_for_youtube(
        title: str, description: str, hashtags: List[str]
    ) -> Tuple[str, str]:
        """Format title and description for YouTube.

        Parameters
        ----------
        title:
            Video title.
        description:
            Full description text.
        hashtags:
            Hashtag list.

        Returns
        -------
        Tuple[str, str]
            ``(title, description)`` — title ≤ 100 chars, description ≤ 5 000 chars.
        """
        tags = CaptionFormatter.sanitize_hashtags(hashtags, max_count=15)
        tag_str = " ".join(f"#{t}" for t in tags)
        full_desc = f"{description}\n\n{tag_str}" if tags else description
        return (title[:100], full_desc[:5000])

    @staticmethod
    def format_for_snapchat(
        caption: str, hashtags: List[str]
    ) -> str:
        """Format a caption for Snapchat Spotlight.

        Parameters
        ----------
        caption:
            Generic caption text.
        hashtags:
            Hashtag list.

        Returns
        -------
        str
            Snapchat-ready caption (≤ 250 chars).
        """
        tags = CaptionFormatter.sanitize_hashtags(hashtags, max_count=3)
        tag_str = " ".join(f"#{t}" for t in tags)
        combined = f"{caption} {tag_str}" if tags else caption
        return combined[:250]

    @staticmethod
    def sanitize_hashtags(
        hashtags: List[str], max_count: int = 30
    ) -> List[str]:
        """Remove duplicates, normalise format, and truncate.

        Parameters
        ----------
        hashtags:
            Raw hashtag list.
        max_count:
            Maximum number to keep.

        Returns
        -------
        List[str]
            Cleaned hashtag list (without ``#`` prefix).
        """
        seen: set = set()
        clean: List[str] = []
        for tag in hashtags:
            t = re.sub(r"[^a-zA-Z0-9_]", "", tag.lower().strip().lstrip("#"))
            if t and t not in seen and len(t) <= 100:
                seen.add(t)
                clean.append(t)
            if len(clean) >= max_count:
                break
        return clean
