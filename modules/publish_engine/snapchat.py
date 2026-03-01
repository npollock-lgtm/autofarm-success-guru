"""
Snapchat Publisher — Spotlight submission implementation.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict

from modules.publish_engine.base import BasePlatformPublisher

logger = logging.getLogger("autofarm.publish_engine.snapchat")

API_BASE = "https://adsapi.snapchat.com/v1"
UPLOAD_TIMEOUT = 600


class SnapchatPublisher(BasePlatformPublisher):
    """Publish videos to Snapchat Spotlight."""

    async def publish(
        self, video_id: int, publish_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Upload and submit a video to Snapchat Spotlight.

        Parameters
        ----------
        video_id:
            Database video ID.
        publish_data:
            Must contain ``video_path``, ``captions``.

        Returns
        -------
        Dict[str, Any]
        """
        try:
            valid, err = self._validate_snapchat(publish_data)
            if not valid:
                return {"success": False, "error": err}

            await self.check_rate_limits("spotlight_post", units=1)
            creds = self.get_credentials()
            session = self.get_session()
            token = creds.get("access_token", "")

            submission_id = await self._upload_spotlight(
                session, token, publish_data
            )

            await self.log_publish_attempt(
                video_id, "success", {"submission_id": submission_id}
            )
            return {
                "success": True,
                "platform_post_id": submission_id,
                "video_url": f"https://www.snapchat.com/spotlight/{submission_id}",
                "published_at": time.time(),
            }
        except Exception as exc:
            logger.error("Snapchat publish failed: %s", exc)
            await self.log_publish_attempt(
                video_id, "failed", {"error": str(exc)}
            )
            return {"success": False, "error": str(exc)}

    async def refresh_token(self) -> bool:
        """Refresh Snapchat OAuth token.

        Returns
        -------
        bool
        """
        try:
            creds = self.get_credentials()
            session = self.get_session()
            resp = session.post(
                "https://accounts.snapchat.com/accounts/oauth2/token",
                data={
                    "client_id": creds.get("client_id", ""),
                    "client_secret": creds.get("client_secret", ""),
                    "grant_type": "refresh_token",
                    "refresh_token": creds.get("refresh_token", ""),
                },
                timeout=30,
            )
            data = resp.json()
            if "access_token" in data:
                self.credential_manager.store_credentials(
                    self.brand_id, self.platform,
                    {**creds, "access_token": data["access_token"],
                     "refresh_token": data.get("refresh_token", creds["refresh_token"])},
                )
                return True
            return False
        except Exception as exc:
            logger.error("Snapchat token refresh failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    async def _upload_spotlight(
        self, session: Any, token: str, publish_data: Dict[str, Any]
    ) -> str:
        """Upload a video to Snapchat Spotlight.

        Returns
        -------
        str
            Submission / media ID.
        """
        video_path = publish_data["video_path"]
        caption = publish_data.get("captions", "")[:250]
        headers = {"Authorization": f"Bearer {token}"}

        with open(video_path, "rb") as f:
            resp = session.post(
                f"{API_BASE}/me/media",
                headers=headers,
                files={"media": (os.path.basename(video_path), f, "video/mp4")},
                data={"description": caption},
                timeout=UPLOAD_TIMEOUT,
            )
        return resp.json().get("media", {}).get("id", "")

    def _validate_snapchat(
        self, publish_data: Dict[str, Any]
    ) -> tuple:
        """Validate Snapchat Spotlight requirements.

        Returns
        -------
        tuple[bool, str]
        """
        vp = publish_data.get("video_path", "")
        if not vp or not os.path.exists(vp):
            return (False, "Video file not found")
        size_mb = os.path.getsize(vp) / (1024 * 1024)
        if size_mb > 32:
            return (False, "Video exceeds Snapchat 32 MB limit")
        return (True, "")
