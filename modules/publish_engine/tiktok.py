"""
TikTok Publisher — Content Posting API implementation.

Uses OAuth 2.0 with chunked upload.  Polling-based publish confirmation.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict

from modules.publish_engine.base import BasePlatformPublisher

logger = logging.getLogger("autofarm.publish_engine.tiktok")

API_BASE = "https://open.tiktokapis.com/v2"
CHUNK_SIZE = 10 * 1024 * 1024  # 10 MB
POLL_INTERVAL = 15  # seconds
POLL_TIMEOUT = 10 * 60  # 10 minutes


class TikTokPublisher(BasePlatformPublisher):
    """Publish videos to TikTok via the Content Posting API.

    Inherits session, credentials, and rate-limit helpers from
    ``BasePlatformPublisher``.
    """

    async def publish(
        self, video_id: int, publish_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Upload and publish a video to TikTok.

        Parameters
        ----------
        video_id:
            Database video ID.
        publish_data:
            Must contain ``video_path``, ``captions``, ``hashtags``.

        Returns
        -------
        Dict[str, Any]
            Result dict with ``success``, ``platform_post_id``, ``video_url``, etc.
        """
        video_path = publish_data["video_path"]
        valid, err = self._validate_tiktok(publish_data)
        if not valid:
            return {"success": False, "error": err}

        try:
            await self.check_rate_limits("upload", units=1)
            creds = self.get_credentials()
            session = self.get_session()

            # 1. Init upload
            upload_url, publish_id = await self._init_upload(
                session, creds, video_path
            )

            # 2. Upload chunks
            await self._upload_chunks(session, video_path, upload_url)

            # 3. Publish with metadata
            await self.check_rate_limits("publish", units=1)
            await self._publish_video(
                session, creds, publish_id, publish_data
            )

            # 4. Poll status
            result = await self._poll_status(session, creds, publish_id)

            await self.log_publish_attempt(video_id, "success", result)
            return {
                "success": True,
                "platform_post_id": result.get("video_id", ""),
                "video_url": f"https://www.tiktok.com/@/video/{result.get('video_id', '')}",
                "published_at": time.time(),
            }
        except Exception as exc:
            logger.error("TikTok publish failed: %s", exc)
            await self.log_publish_attempt(
                video_id, "failed", {"error": str(exc)}
            )
            return {"success": False, "error": str(exc)}

    async def refresh_token(self) -> bool:
        """Refresh TikTok OAuth access token.

        Returns
        -------
        bool
        """
        try:
            creds = self.get_credentials()
            session = self.get_session()
            resp = session.post(
                f"{API_BASE}/oauth/token/",
                json={
                    "client_key": creds.get("client_key", ""),
                    "client_secret": creds.get("client_secret", ""),
                    "grant_type": "refresh_token",
                    "refresh_token": creds.get("refresh_token", ""),
                },
                timeout=30,
            )
            data = resp.json()
            if "access_token" in data:
                self.credential_manager.store_credentials(
                    self.brand_id,
                    self.platform,
                    {
                        **creds,
                        "access_token": data["access_token"],
                        "refresh_token": data.get("refresh_token", creds["refresh_token"]),
                    },
                )
                return True
            return False
        except Exception as exc:
            logger.error("TikTok token refresh failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _init_upload(
        self, session: Any, creds: Dict, video_path: str
    ) -> tuple:
        """Initiate a TikTok video upload.

        Returns
        -------
        tuple[str, str]
            ``(upload_url, publish_id)``.
        """
        file_size = os.path.getsize(video_path)
        headers = {"Authorization": f"Bearer {creds['access_token']}"}
        resp = session.post(
            f"{API_BASE}/post/publish/inbox/video/init/",
            headers=headers,
            json={
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": file_size,
                    "chunk_size": CHUNK_SIZE,
                    "total_chunk_count": max(
                        1, (file_size + CHUNK_SIZE - 1) // CHUNK_SIZE
                    ),
                },
            },
            timeout=30,
        )
        data = resp.json().get("data", {})
        return (data.get("upload_url", ""), data.get("publish_id", ""))

    async def _upload_chunks(
        self, session: Any, video_path: str, upload_url: str
    ) -> None:
        """Upload video file in chunks."""
        with open(video_path, "rb") as f:
            part = 0
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                session.put(
                    upload_url,
                    data=chunk,
                    headers={
                        "Content-Type": "video/mp4",
                        "Content-Range": (
                            f"bytes {part * CHUNK_SIZE}-"
                            f"{part * CHUNK_SIZE + len(chunk) - 1}/"
                            f"{os.path.getsize(video_path)}"
                        ),
                    },
                    timeout=120,
                )
                part += 1

    async def _publish_video(
        self,
        session: Any,
        creds: Dict,
        publish_id: str,
        publish_data: Dict[str, Any],
    ) -> None:
        """Finalize the publish with metadata."""
        caption = publish_data.get("captions", "")[:2200]
        headers = {"Authorization": f"Bearer {creds['access_token']}"}
        session.post(
            f"{API_BASE}/post/publish/video/init/",
            headers=headers,
            json={
                "publish_id": publish_id,
                "post_info": {
                    "title": caption[:150],
                    "description": caption,
                    "disable_comment": False,
                    "privacy_level": "PUBLIC_TO_EVERYONE",
                },
            },
            timeout=30,
        )

    async def _poll_status(
        self, session: Any, creds: Dict, publish_id: str
    ) -> Dict[str, Any]:
        """Poll TikTok for publish completion."""
        import asyncio

        headers = {"Authorization": f"Bearer {creds['access_token']}"}
        deadline = time.time() + POLL_TIMEOUT
        while time.time() < deadline:
            resp = session.post(
                f"{API_BASE}/post/publish/status/fetch/",
                headers=headers,
                json={"publish_id": publish_id},
                timeout=15,
            )
            data = resp.json().get("data", {})
            status = data.get("status", "")
            if status == "PUBLISH_COMPLETE":
                return data
            if status in ("FAILED", "PUBLISH_FAILED"):
                raise RuntimeError(f"TikTok publish failed: {data}")
            await asyncio.sleep(POLL_INTERVAL)
        raise TimeoutError("TikTok publish poll timed out")

    def _validate_tiktok(
        self, publish_data: Dict[str, Any]
    ) -> tuple:
        """Validate TikTok-specific requirements.

        Returns
        -------
        tuple[bool, str]
        """
        vp = publish_data.get("video_path", "")
        if not vp or not os.path.exists(vp):
            return (False, "Video file not found")
        size_gb = os.path.getsize(vp) / (1024 ** 3)
        if size_gb > 4:
            return (False, "Video exceeds TikTok 4GB limit")
        return (True, "")
