"""
Authoritative platform rate limit definitions for AutoFarm Zero v6.0.

Sources: Official platform developer documentation (verified current).
All rate limits, quota costs, and content specifications for every
platform supported by the system. These constants are the single source
of truth for platform limitations.
"""

PLATFORM_LIMITS = {
    "youtube": {
        "quota_units_per_day": 10000,
        "cost_per_upload": 1600,
        "cost_per_metadata_update": 50,
        "cost_per_thumbnail_upload": 50,
        "cost_per_analytics_read": 1,
        "max_uploads_per_day_per_channel": 6,
        "max_channels_per_gcp_project": 1,
        "min_video_gap_minutes": 60,
        "shorts_max_duration_seconds": 60,
        "shorts_aspect_ratio": "9:16",
        "max_title_length": 100,
        "max_description_length": 5000,
        "max_tags": 500,
        "max_resolution": "1920x1080",
        "tos_key_points": [
            "Do not upload identical content to multiple channels",
            "Do not use automation to create misleading engagement",
            "Disclose AI-generated content where required",
            "No spam, deceptive practices, or misleading metadata",
            "Must comply with YouTube's Repetitive Content policy",
        ],
    },
    "instagram": {
        "graph_api_calls_per_hour": 200,
        "content_publishing_calls_per_hour": 25,
        "max_posts_per_24h_per_account": 25,
        "recommended_posts_per_day": 2,
        "reels_container_poll_interval_seconds": 15,
        "reels_container_max_wait_minutes": 10,
        "min_post_gap_minutes": 180,
        "max_caption_length": 2200,
        "max_hashtags": 30,
        "max_video_size_mb": 4096,
        "reels_max_duration_seconds": 90,
        "reels_aspect_ratio": "9:16",
        "tos_key_points": [
            "Authentic interactions only — no automated likes/comments",
            "Do not use third-party tools that violate platform terms",
            "Must have Instagram Business or Creator account",
            "AI content label recommended where applicable",
            "Do not post coordinated inauthentic content across accounts",
        ],
    },
    "facebook": {
        "graph_api_calls_per_hour": 200,
        "page_post_calls_per_hour": 25,
        "max_posts_per_day_per_page": 25,
        "recommended_posts_per_day": 1,
        "min_post_gap_minutes": 240,
        "video_max_size_gb": 10,
        "video_max_duration_minutes": 241,
        "reels_max_duration_seconds": 90,
        "max_message_length": 63206,
        "tos_key_points": [
            "No coordinated inauthentic behaviour",
            "Page must be authentic representation",
            "Video content must be original or properly licensed",
            "No artificial engagement",
            "Branded content policies apply for sponsored mentions",
        ],
    },
    "tiktok": {
        "content_posting_api_videos_per_day": 5,
        "recommended_videos_per_day": 2,
        "min_post_gap_minutes": 180,
        "oauth_token_expires_hours": 24,
        "refresh_token_expires_days": 365,
        "max_video_size_mb": 4096,
        "max_video_duration_seconds": 600,
        "short_video_max_seconds": 60,
        "min_video_duration_seconds": 3,
        "max_title_length": 2200,
        "max_hashtags_recommended": 5,
        "chunk_size_mb": 10,
        "tos_key_points": [
            "Must use official Content Posting API",
            "No automation that violates platform policies",
            "Synthetic or AI content must be labelled using TikTok's AI Content label",
            "No spam or coordinated inauthentic content",
            "Creator accounts must comply with Community Guidelines",
            "Do not use scrapers or unofficial APIs",
        ],
    },
    "snapchat": {
        "spotlight_max_per_day": 10,
        "recommended_per_day": 1,
        "max_video_size_mb": 32,
        "max_video_duration_seconds": 60,
        "min_video_duration_seconds": 5,
        "max_caption_length": 250,
        "aspect_ratio": "9:16",
        "min_resolution": "1080x1920",
        "tos_key_points": [
            "Content must meet Spotlight eligibility criteria",
            "No misleading or deceptive content",
            "Original content only",
            "Must comply with Snap's Community Guidelines",
            "AI-generated content should follow disclosure guidelines",
        ],
    },
}


def get_platform_limit(platform: str, limit_key: str, default=None):
    """
    Retrieves a specific limit value for a platform.

    Parameters:
        platform: Platform name (youtube, instagram, etc.).
        limit_key: The limit key to look up.
        default: Default value if not found.

    Returns:
        The limit value, or default if not found.
    """
    return PLATFORM_LIMITS.get(platform, {}).get(limit_key, default)


def get_min_post_gap(platform: str) -> int:
    """
    Returns the minimum gap in minutes between posts for a platform.

    Parameters:
        platform: Platform name.

    Returns:
        Minimum gap in minutes.
    """
    return get_platform_limit(platform, 'min_post_gap_minutes', 60)


def get_daily_post_limit(platform: str) -> int:
    """
    Returns the recommended daily post limit for a platform.

    Parameters:
        platform: Platform name.

    Returns:
        Recommended daily post count.
    """
    limit_keys = {
        'youtube': 'max_uploads_per_day_per_channel',
        'instagram': 'recommended_posts_per_day',
        'facebook': 'recommended_posts_per_day',
        'tiktok': 'recommended_videos_per_day',
        'snapchat': 'recommended_per_day',
    }
    key = limit_keys.get(platform, 'recommended_posts_per_day')
    return get_platform_limit(platform, key, 1)
