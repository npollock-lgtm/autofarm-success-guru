"""
Base Platform Publisher — abstract base class for all platform publishers.

All publishing calls go through ``BrandIPRouter`` for network isolation and
``RateLimitManager`` for compliance.  Credentials are retrieved via
``CredentialManager``.
"""

from __future__ import annotations

import abc
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("autofarm.publish_engine.base")


class BasePlatformPublisher(abc.ABC):
    """Abstract base for every platform-specific publisher.

    Parameters
    ----------
    brand_id:
        Brand identifier.
    platform:
        Platform name (``tiktok``, ``instagram``, etc.).
    ip_router:
        ``BrandIPRouter`` instance for session management.
    rate_limiter:
        ``RateLimitManager`` for API call gating.
    credential_manager:
        ``CredentialManager`` for token access.
    db:
        Database helper instance.
    """

    def __init__(
        self,
        brand_id: str,
        platform: str,
        ip_router: Any,
        rate_limiter: Any,
        credential_manager: Any,
        db: Any,
    ) -> None:
        self.brand_id = brand_id
        self.platform = platform
        self.ip_router = ip_router
        self.rate_limiter = rate_limiter
        self.credential_manager = credential_manager
        self.db = db

    # ------------------------------------------------------------------
    # Session / credentials
    # ------------------------------------------------------------------

    def get_session(self) -> Any:
        """Return a ``requests.Session`` routed through BrandIPRouter.

        Returns
        -------
        requests.Session
            Configured session for this brand.
        """
        return self.ip_router.get_session(self.brand_id)

    def get_credentials(self) -> Dict[str, Any]:
        """Retrieve decrypted credentials for brand × platform.

        Returns
        -------
        Dict[str, Any]
            ``{access_token, refresh_token, account_id, …}``.
        """
        return self.credential_manager.get_credentials(
            self.brand_id, self.platform
        )

    # ------------------------------------------------------------------
    # Rate limit
    # ------------------------------------------------------------------

    async def check_rate_limits(
        self, endpoint: str, units: int = 1
    ) -> bool:
        """Verify publishing will not exceed rate limits.

        Parameters
        ----------
        endpoint:
            API endpoint identifier.
        units:
            Cost units for this call.

        Returns
        -------
        bool
            ``True`` if within limits.

        Raises
        ------
        Exception
            If rate limit would be breached.
        """
        return await self.rate_limiter.acquire(
            f"{self.platform}_{endpoint}", units=units
        )

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @abc.abstractmethod
    async def publish(
        self, video_id: int, publish_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Publish content to the platform.

        Parameters
        ----------
        video_id:
            Database video ID.
        publish_data:
            Dict with keys: ``video_path``, ``thumbnail_path``, ``title``,
            ``description``, ``captions``, ``hashtags``, ``brand_config``,
            ``scheduled_time``.

        Returns
        -------
        Dict[str, Any]
            ``{success, platform_post_id, video_url, published_at, error}``.
        """

    @abc.abstractmethod
    async def refresh_token(self) -> bool:
        """Refresh the OAuth token for this brand × platform.

        Returns
        -------
        bool
            ``True`` if refresh succeeded.
        """

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_publish_params(
        self, publish_data: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """Validate publish data against platform limits.

        Parameters
        ----------
        publish_data:
            Publish payload.

        Returns
        -------
        Tuple[bool, str]
            ``(is_valid, error_message)``.
        """
        if not publish_data.get("video_path"):
            return (False, "Missing video_path")
        return (True, "")

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    async def log_publish_attempt(
        self,
        video_id: int,
        status: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Log a publish attempt to the database.

        Parameters
        ----------
        video_id:
            Video primary key.
        status:
            Result status (``success``, ``failed``, ``retry``).
        metadata:
            Optional extra info (error messages, etc.).

        Side Effects
        ------------
        Inserts a row into ``publish_log``.
        """
        import json

        await self.db.execute(
            """
            INSERT INTO publish_log
                (video_id, brand_id, platform, status, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                video_id,
                self.brand_id,
                self.platform,
                status,
                json.dumps(metadata or {}),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        logger.info(
            "Publish %s: video=%d brand=%s platform=%s",
            status, video_id, self.brand_id, self.platform,
        )
