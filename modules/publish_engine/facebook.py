"""
Facebook Publisher — Graph API video publisher.

Posts videos to a Facebook Page using a page access token.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict

from modules.publish_engine.base import BasePlatformPublisher

logger = logging.getLogger("autofarm.publish_engine.facebook")

API_BASE = "https://graph.facebook.com/v18.0"
UPLOAD_TIMEOUT = 600  # 10 min


class FacebookPublisher(BasePlatformPublisher):
    """Publish videos to a Facebook Page via the Graph API."""

    async def publish(
        self, video_id: int, publish_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Upload and publish a video to Facebook.

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
            await self.check_rate_limits("video_post", units=1)
            creds = self.get_credentials()
            session = self.get_session()
            page_id = creds.get("account_id", "")
            token = creds.get("access_token", "")
            video_path = publish_data["video_path"]

            caption = self._build_caption(publish_data)

            post_id = await self._upload_and_post(
                session, page_id, token, video_path, caption, publish_data
            )

            url = f"https://www.facebook.com/{page_id}/posts/{post_id}"
            await self.log_publish_attempt(video_id, "success", {"post_id": post_id})
            return {
                "success": True,
                "platform_post_id": post_id,
                "video_url": url,
                "published_at": time.time(),
            }
        except Exception as exc:
            logger.error("Facebook publish failed: %s", exc)
            await self.log_publish_attempt(video_id, "failed", {"error": str(exc)})
            return {"success": False, "error": str(exc)}

    async def refresh_token(self) -> bool:
        """Refresh Facebook long-lived page token.

        Returns
        -------
        bool
        """
        try:
            creds = self.get_credentials()
            session = self.get_session()
            resp = session.get(
                f"{API_BASE}/oauth/access_token",
                params={
                    "grant_type": "fb_exchange_token",
                    "client_id": creds.get("app_id", ""),
                    "client_secret": creds.get("app_secret", ""),
                    "fb_exchange_token": creds.get("access_token", ""),
                },
                timeout=30,
            )
            data = resp.json()
            if "access_token" in data:
                self.credential_manager.store_credentials(
                    self.brand_id, self.platform,
                    {**creds, "access_token": data["access_token"]},
                )
                return True
            return False
        except Exception as exc:
            logger.error("Facebook token refresh failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    async def _upload_and_post(
        self, session: Any, page_id: str, token: str,
        video_path: str, caption: str, publish_data: Dict[str, Any],
    ) -> str:
        """Upload a video and create a page post.

        Returns
        -------
        str
            Post ID.
        """
        scheduled_time = publish_data.get("scheduled_time")

        with open(video_path, "rb") as f:
            data: Dict[str, Any] = {
                "description": caption[:2000],
                "access_token": token,
            }
            if scheduled_time:
                data["scheduled_publish_time"] = int(scheduled_time)
                data["published"] = False

            resp = session.post(
                f"{API_BASE}/{page_id}/videos",
                data=data,
                files={"source": f},
                timeout=UPLOAD_TIMEOUT,
            )
        return resp.json().get("id", "")

    @staticmethod
    def _build_caption(publish_data: Dict[str, Any]) -> str:
        """Build a Facebook caption."""
        caption = publish_data.get("captions", "")
        hashtags = publish_data.get("hashtags", [])
        if hashtags:
            tag_str = " ".join(f"#{h}" for h in hashtags[:10])
            caption = f"{caption}\n\n{tag_str}"
        return caption[:2000]
