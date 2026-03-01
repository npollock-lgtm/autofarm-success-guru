"""
Instagram Publisher — Graph API (Reels) implementation.

Creates a media container, uploads the video, polls until ready,
then publishes.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict

from modules.publish_engine.base import BasePlatformPublisher

logger = logging.getLogger("autofarm.publish_engine.instagram")

API_BASE = "https://graph.instagram.com/v18.0"
CONTAINER_POLL_INTERVAL = 15  # seconds
CONTAINER_MAX_WAIT = 10 * 60  # 10 minutes


class InstagramPublisher(BasePlatformPublisher):
    """Publish Reels to Instagram via the Graph API."""

    async def publish(
        self, video_id: int, publish_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Upload and publish an Instagram Reel.

        Parameters
        ----------
        video_id:
            Database video ID.
        publish_data:
            Must contain ``video_path``, ``captions``, ``hashtags``.

        Returns
        -------
        Dict[str, Any]
        """
        try:
            await self.check_rate_limits("media_create", units=1)
            creds = self.get_credentials()
            session = self.get_session()
            user_id = creds.get("account_id", "")
            token = creds.get("access_token", "")

            caption = self._build_caption(publish_data)

            # 1. Create container
            container_id = await self._create_container(
                session, user_id, token, caption, publish_data["video_path"]
            )

            # 2. Poll until ready
            ready = await self._poll_container(session, container_id, token)
            if not ready:
                raise TimeoutError("Instagram container not ready in time")

            # 3. Publish
            await self.check_rate_limits("media_publish", units=1)
            media_id = await self._publish_container(
                session, user_id, container_id, token
            )

            url = f"https://www.instagram.com/reel/{media_id}/"
            await self.log_publish_attempt(video_id, "success", {"media_id": media_id})
            return {
                "success": True,
                "platform_post_id": media_id,
                "video_url": url,
                "published_at": time.time(),
            }
        except Exception as exc:
            logger.error("Instagram publish failed: %s", exc)
            await self.log_publish_attempt(
                video_id, "failed", {"error": str(exc)}
            )
            return {"success": False, "error": str(exc)}

    async def refresh_token(self) -> bool:
        """Refresh Instagram long-lived token.

        Returns
        -------
        bool
        """
        try:
            creds = self.get_credentials()
            session = self.get_session()
            resp = session.get(
                f"{API_BASE}/refresh_access_token",
                params={
                    "grant_type": "ig_refresh_token",
                    "access_token": creds.get("access_token", ""),
                },
                timeout=30,
            )
            data = resp.json()
            if "access_token" in data:
                self.credential_manager.store_credentials(
                    self.brand_id,
                    self.platform,
                    {**creds, "access_token": data["access_token"]},
                )
                return True
            return False
        except Exception as exc:
            logger.error("Instagram token refresh failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _create_container(
        self, session: Any, user_id: str, token: str, caption: str, video_path: str
    ) -> str:
        """Create a media container for the Reel.

        Returns
        -------
        str
            Container ID.
        """
        resp = session.post(
            f"{API_BASE}/{user_id}/media",
            data={
                "media_type": "REELS",
                "caption": caption[:2200],
                "video_url": video_path,  # or hosted URL
                "access_token": token,
            },
            timeout=60,
        )
        return resp.json().get("id", "")

    async def _poll_container(
        self, session: Any, container_id: str, token: str
    ) -> bool:
        """Poll container status until ``FINISHED``.

        Returns
        -------
        bool
        """
        import asyncio

        deadline = time.time() + CONTAINER_MAX_WAIT
        while time.time() < deadline:
            resp = session.get(
                f"{API_BASE}/{container_id}",
                params={"fields": "status_code", "access_token": token},
                timeout=15,
            )
            status = resp.json().get("status_code", "")
            if status == "FINISHED":
                return True
            if status == "ERROR":
                raise RuntimeError("Instagram container creation failed")
            await asyncio.sleep(CONTAINER_POLL_INTERVAL)
        return False

    async def _publish_container(
        self, session: Any, user_id: str, container_id: str, token: str
    ) -> str:
        """Publish a finished container.

        Returns
        -------
        str
            Media ID.
        """
        resp = session.post(
            f"{API_BASE}/{user_id}/media_publish",
            data={"creation_id": container_id, "access_token": token},
            timeout=60,
        )
        return resp.json().get("id", "")

    @staticmethod
    def _build_caption(publish_data: Dict[str, Any]) -> str:
        """Build an Instagram caption from publish data.

        Returns
        -------
        str
        """
        caption = publish_data.get("captions", "")
        hashtags = publish_data.get("hashtags", [])
        if hashtags:
            tag_str = " ".join(f"#{h}" for h in hashtags[:30])
            if len(caption) + len(tag_str) + 2 <= 2200:
                caption = f"{caption}\n\n{tag_str}"
        return caption[:2200]
