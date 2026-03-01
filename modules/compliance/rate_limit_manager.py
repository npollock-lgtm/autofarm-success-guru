"""
Central rate limit enforcement for AutoFarm Zero — Success Guru Network v6.0.

Every platform API call MUST pass through RateLimitManager.check_and_increment().
No exceptions. Tracks calls per brand per platform per endpoint per time window.
Raises RateLimitExceeded before making a call that would breach limits.

Thread-safe via SQLite WAL mode + application-level write lock.
"""

import logging
from datetime import datetime, timedelta

from database.db import Database
from modules.compliance.rate_limits import PLATFORM_LIMITS, get_min_post_gap

logger = logging.getLogger(__name__)


class RateLimitExceeded(Exception):
    """Raised when a rate limit would be breached."""

    def __init__(self, brand_id: str, platform: str, endpoint: str,
                 retry_after_seconds: int = 0, message: str = ""):
        """
        Parameters:
            brand_id: Brand that would breach the limit.
            platform: Platform being accessed.
            endpoint: API endpoint being called.
            retry_after_seconds: Seconds until the call can be retried.
            message: Human-readable explanation.
        """
        self.brand_id = brand_id
        self.platform = platform
        self.endpoint = endpoint
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            message or f"Rate limit exceeded for {brand_id}/{platform}/{endpoint}. "
            f"Retry after {retry_after_seconds}s"
        )


class RateLimitManager:
    """
    Central rate limit enforcement for all platform API calls.

    Tracks calls per brand per platform per endpoint per time window
    (hourly and daily). Raises RateLimitExceeded before making a call
    that would breach limits. All counters stored in SQLite for
    persistence across process restarts.
    """

    def __init__(self):
        """
        Initializes the RateLimitManager with database access.
        """
        self.db = Database()

    def check_and_increment(self, brand_id: str, platform: str,
                            endpoint: str, units: int = 1) -> bool:
        """
        Checks if this call is within rate limits. If yes, increments
        counter and returns True. If no, raises RateLimitExceeded.

        Parameters:
            brand_id: The brand making the API call.
            platform: Target platform.
            endpoint: API endpoint being called (e.g. 'upload', 'metadata', 'analytics').
            units: Number of quota units consumed (e.g. YouTube quota units).

        Returns:
            True if the call is permitted.

        Raises:
            RateLimitExceeded: If the call would breach any limit.

        Side effects:
            Increments the rate limit counter in the database.
        """
        # Check hourly limits
        self._check_hourly_limit(brand_id, platform, endpoint, units)

        # Check daily limits
        self._check_daily_limit(brand_id, platform, endpoint, units)

        # Check minimum post gap
        if endpoint in ('upload', 'publish', 'post'):
            self._check_post_gap(brand_id, platform)

        # All checks passed — increment counters
        self.db.increment_rate_limit(brand_id, platform, endpoint, 'hourly', units)
        self.db.increment_rate_limit(brand_id, platform, endpoint, 'daily', units)

        return True

    def _check_hourly_limit(self, brand_id: str, platform: str,
                            endpoint: str, units: int) -> None:
        """
        Checks hourly rate limits for the given API call.

        Parameters:
            brand_id: Brand identifier.
            platform: Platform name.
            endpoint: API endpoint.
            units: Quota units for this call.

        Raises:
            RateLimitExceeded: If hourly limit would be breached.
        """
        limits = PLATFORM_LIMITS.get(platform, {})

        # Determine the relevant hourly limit
        hourly_limit = None
        if platform in ('instagram', 'facebook'):
            if endpoint == 'publish':
                hourly_limit = limits.get('content_publishing_calls_per_hour', 25)
            else:
                hourly_limit = limits.get('graph_api_calls_per_hour', 200)
        elif platform == 'youtube':
            # YouTube uses quota units, not call counts for hourly
            pass

        if hourly_limit is None:
            return

        current = self.db.get_rate_limit_count(brand_id, platform, endpoint, 'hourly')
        current_count = current['count'] if current else 0

        if current_count + 1 > hourly_limit:
            raise RateLimitExceeded(
                brand_id=brand_id,
                platform=platform,
                endpoint=endpoint,
                retry_after_seconds=3600,
                message=f"Hourly limit ({hourly_limit}) reached for "
                        f"{brand_id}/{platform}/{endpoint}"
            )

    def _check_daily_limit(self, brand_id: str, platform: str,
                           endpoint: str, units: int) -> None:
        """
        Checks daily rate limits and quota for the given API call.

        Parameters:
            brand_id: Brand identifier.
            platform: Platform name.
            endpoint: API endpoint.
            units: Quota units for this call.

        Raises:
            RateLimitExceeded: If daily limit would be breached.
        """
        limits = PLATFORM_LIMITS.get(platform, {})

        if platform == 'youtube' and endpoint == 'upload':
            # Check quota units
            daily_record = self.db.get_rate_limit_count(
                brand_id, platform, 'quota_units', 'daily'
            )
            current_units = daily_record['units'] if daily_record else 0
            quota_limit = limits.get('quota_units_per_day', 10000)

            if current_units + units > quota_limit:
                raise RateLimitExceeded(
                    brand_id=brand_id,
                    platform=platform,
                    endpoint=endpoint,
                    retry_after_seconds=86400,
                    message=f"YouTube quota ({current_units + units}/{quota_limit} units) "
                            f"would be exceeded"
                )

            # Also track quota units
            self.db.increment_rate_limit(
                brand_id, platform, 'quota_units', 'daily', units
            )

        elif platform == 'tiktok' and endpoint in ('upload', 'publish'):
            daily_limit = limits.get('content_posting_api_videos_per_day', 5)
            daily_record = self.db.get_rate_limit_count(
                brand_id, platform, endpoint, 'daily'
            )
            current_count = daily_record['count'] if daily_record else 0

            if current_count + 1 > daily_limit:
                raise RateLimitExceeded(
                    brand_id=brand_id,
                    platform=platform,
                    endpoint=endpoint,
                    retry_after_seconds=86400,
                    message=f"TikTok daily limit ({daily_limit}) reached"
                )

        elif platform == 'snapchat' and endpoint in ('upload', 'publish'):
            daily_limit = limits.get('spotlight_max_per_day', 10)
            daily_record = self.db.get_rate_limit_count(
                brand_id, platform, endpoint, 'daily'
            )
            current_count = daily_record['count'] if daily_record else 0

            if current_count + 1 > daily_limit:
                raise RateLimitExceeded(
                    brand_id=brand_id,
                    platform=platform,
                    endpoint=endpoint,
                    retry_after_seconds=86400,
                    message=f"Snapchat daily limit ({daily_limit}) reached"
                )

    def _check_post_gap(self, brand_id: str, platform: str) -> None:
        """
        Checks minimum time gap since last post.

        Parameters:
            brand_id: Brand identifier.
            platform: Platform name.

        Raises:
            RateLimitExceeded: If posting too soon after last post.
        """
        min_gap = get_min_post_gap(platform)
        last_publish = self.db.get_last_publish_time(brand_id, platform)

        if last_publish:
            try:
                last_dt = datetime.fromisoformat(last_publish)
                elapsed = (datetime.utcnow() - last_dt).total_seconds() / 60
                if elapsed < min_gap:
                    wait_seconds = int((min_gap - elapsed) * 60)
                    raise RateLimitExceeded(
                        brand_id=brand_id,
                        platform=platform,
                        endpoint='publish',
                        retry_after_seconds=wait_seconds,
                        message=f"Min post gap not met. {elapsed:.0f}min since last post, "
                                f"need {min_gap}min"
                    )
            except ValueError:
                pass

    def get_remaining_quota(self, brand_id: str, platform: str) -> dict:
        """
        Returns remaining quota information for a brand on a platform.

        Parameters:
            brand_id: Brand identifier.
            platform: Platform name.

        Returns:
            Dictionary with daily_remaining, hourly_remaining, can_upload, next_reset_utc.
        """
        limits = PLATFORM_LIMITS.get(platform, {})

        # Daily
        daily_record = self.db.get_rate_limit_count(
            brand_id, platform, 'upload', 'daily'
        )
        daily_count = daily_record['count'] if daily_record else 0

        # Get daily max
        if platform == 'tiktok':
            daily_max = limits.get('content_posting_api_videos_per_day', 5)
        elif platform == 'youtube':
            daily_max = limits.get('max_uploads_per_day_per_channel', 6)
        elif platform == 'snapchat':
            daily_max = limits.get('spotlight_max_per_day', 10)
        else:
            daily_max = limits.get('max_posts_per_day_per_page', 25)

        # Today's publishes
        publishes_today = self.db.count_publishes_today(brand_id, platform)

        return {
            'daily_remaining': max(0, daily_max - publishes_today),
            'daily_limit': daily_max,
            'publishes_today': publishes_today,
            'can_upload': publishes_today < daily_max,
            'next_reset_utc': (
                datetime.utcnow().replace(hour=0, minute=0, second=0)
                + timedelta(days=1)
            ).isoformat(),
        }

    def reset_hourly_counters(self) -> None:
        """
        Resets all hourly rate limit counters. Called every hour by cron.

        Side effects:
            Deletes all hourly rate limit records from the database.
        """
        self.db.pool.write_with_lock(
            "DELETE FROM rate_limits WHERE window_type = 'hourly'"
        )
        logger.info("Hourly rate limit counters reset")

    def reset_daily_counters(self) -> None:
        """
        Resets all daily rate limit counters. Called at midnight UTC.

        Side effects:
            Deletes all daily rate limit records from the database.
        """
        self.db.pool.write_with_lock(
            "DELETE FROM rate_limits WHERE window_type = 'daily'"
        )
        logger.info("Daily rate limit counters reset")

    def get_network_wide_status(self) -> dict:
        """
        Returns quota status for all brands across all platforms.

        Returns:
            Nested dictionary: {brand_id: {platform: quota_info}}.
        """
        from config.settings import get_all_brand_platforms
        status = {}
        for brand_id, platform in get_all_brand_platforms():
            if brand_id not in status:
                status[brand_id] = {}
            status[brand_id][platform] = self.get_remaining_quota(brand_id, platform)
        return status

    def estimate_next_available_slot(self, brand_id: str, platform: str) -> datetime:
        """
        Calculates when next upload is possible for a brand on a platform.

        Parameters:
            brand_id: Brand identifier.
            platform: Platform name.

        Returns:
            Datetime of next available upload slot.
        """
        quota = self.get_remaining_quota(brand_id, platform)

        if quota['can_upload']:
            # Check post gap
            last_publish = self.db.get_last_publish_time(brand_id, platform)
            if last_publish:
                try:
                    last_dt = datetime.fromisoformat(last_publish)
                    min_gap = get_min_post_gap(platform)
                    next_available = last_dt + timedelta(minutes=min_gap)
                    if next_available > datetime.utcnow():
                        return next_available
                except ValueError:
                    pass
            return datetime.utcnow()
        else:
            # Daily limit reached — next slot is after midnight UTC
            return datetime.fromisoformat(quota['next_reset_utc'])
