"""
YouTube Publisher — Data API v3 resumable upload implementation.

WARNING: Most complex publisher due to quota management (1 600 units / upload).
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict

from modules.publish_engine.base import BasePlatformPublisher

logger = logging.getLogger("autofarm.publish_engine.youtube")

API_BASE = "https://www.googleapis.com/youtube/v3"
UPLOAD_BASE = "https://www.googleapis.com/upload/youtube/v3/videos"
CHUNK_SIZE = 10 * 1024 * 1024  # 10 MB
UPLOAD_TIMEOUT = 1200  # 20 min
UPLOAD_QUOTA_UNITS = 1600
THUMBNAIL_QUOTA_UNITS = 50


class YouTubePublisher(BasePlatformPublisher):
    """Publish videos to YouTube via the Data API v3."""

    async def publish(
        self, video_id: int, publish_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Upload and publish a video to YouTube.

        Parameters
        ----------
        video_id:
            Database video ID.
        publish_data:
            Must contain ``video_path``, ``title``, ``description``,
            ``hashtags``, ``thumbnail_path``.

        Returns
        -------
        Dict[str, Any]
        """
        try:
            await self.check_rate_limits("upload", units=UPLOAD_QUOTA_UNITS)
            creds = self.get_credentials()
            session = self.get_session()
            token = creds.get("access_token", "")
            video_path = publish_data["video_path"]

            metadata = self._build_metadata(publish_data)

            # 1. Resumable upload
            yt_video_id = await self._upload_resumable(
                session, token, video_path, metadata
            )

            # 2. Thumbnail
            thumb = publish_data.get("thumbnail_path", "")
            if thumb and os.path.exists(thumb):
                await self.check_rate_limits("thumbnail", units=THUMBNAIL_QUOTA_UNITS)
                await self._upload_thumbnail(session, token, yt_video_id, thumb)

            url = f"https://www.youtube.com/watch?v={yt_video_id}"
            await self.log_publish_attempt(video_id, "success", {"yt_id": yt_video_id})
            return {
                "success": True,
                "platform_post_id": yt_video_id,
                "video_url": url,
                "published_at": time.time(),
            }
        except Exception as exc:
            logger.error("YouTube publish failed: %s", exc)
            await self.log_publish_attempt(video_id, "failed", {"error": str(exc)})
            return {"success": False, "error": str(exc)}

    async def refresh_token(self) -> bool:
        """Refresh YouTube OAuth access token.

        Returns
        -------
        bool
        """
        try:
            creds = self.get_credentials()
            session = self.get_session()
            resp = session.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": creds.get("client_id", ""),
                    "client_secret": creds.get("client_secret", ""),
                    "refresh_token": creds.get("refresh_token", ""),
                    "grant_type": "refresh_token",
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
            logger.error("YouTube token refresh failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    async def _upload_resumable(
        self, session: Any, token: str, video_path: str, metadata: Dict
    ) -> str:
        """Perform a resumable upload to YouTube.

        Returns
        -------
        str
            YouTube video ID.
        """
        file_size = os.path.getsize(video_path)
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Length": str(file_size),
            "X-Upload-Content-Type": "video/mp4",
        }

        # 1. Init resumable session
        resp = session.post(
            f"{UPLOAD_BASE}?uploadType=resumable&part=snippet,status",
            headers=headers,
            json=metadata,
            timeout=30,
        )
        resumable_url = resp.headers.get("Location", "")
        if not resumable_url:
            raise RuntimeError("Failed to get resumable upload URL")

        # 2. Upload chunks
        with open(video_path, "rb") as f:
            offset = 0
            while offset < file_size:
                chunk = f.read(CHUNK_SIZE)
                end = offset + len(chunk) - 1
                chunk_headers = {
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "video/mp4",
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {offset}-{end}/{file_size}",
                }
                resp = session.put(
                    resumable_url,
                    headers=chunk_headers,
                    data=chunk,
                    timeout=UPLOAD_TIMEOUT,
                )
                offset += len(chunk)

                # Final chunk returns the video resource
                if resp.status_code in (200, 201):
                    video_data = resp.json()
                    return video_data.get("id", "")

        raise RuntimeError("Upload completed without receiving video ID")

    async def _upload_thumbnail(
        self, session: Any, token: str, yt_video_id: str, thumb_path: str
    ) -> bool:
        """Upload a custom thumbnail.

        Returns
        -------
        bool
        """
        try:
            with open(thumb_path, "rb") as f:
                headers = {
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "image/jpeg",
                }
                resp = session.post(
                    f"{API_BASE}/thumbnails/set?videoId={yt_video_id}",
                    headers=headers,
                    data=f.read(),
                    timeout=60,
                )
            return resp.status_code == 200
        except Exception as exc:
            logger.warning("Thumbnail upload failed: %s", exc)
            return False

    @staticmethod
    def _build_metadata(publish_data: Dict[str, Any]) -> Dict[str, Any]:
        """Build YouTube video metadata.

        Returns
        -------
        Dict[str, Any]
        """
        title = publish_data.get("title", "")[:100]
        description = publish_data.get("description", "")
        hashtags = publish_data.get("hashtags", [])
        if hashtags:
            tag_str = " ".join(f"#{h}" for h in hashtags[:15])
            description = f"{description}\n\n{tag_str}"
        description = description[:5000]

        scheduled_time = publish_data.get("scheduled_time")
        privacy = "private"
        publish_at = None
        if scheduled_time:
            privacy = "private"
            publish_at = scheduled_time

        status: Dict[str, Any] = {
            "privacyStatus": privacy,
            "madeForKids": False,
            "selfDeclaredMadeForKids": False,
        }
        if publish_at:
            status["publishAt"] = publish_at

        return {
            "snippet": {
                "title": title,
                "description": description,
                "tags": hashtags[:25],
                "categoryId": "22",
                "defaultLanguage": "en",
            },
            "status": status,
        }
