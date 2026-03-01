"""
Telegram Reviewer — sends review packages via Telegram Bot API.

**Primary review channel** in AutoFarm V6.0.

Review package contents:
  1. Thumbnail photo (≈100 KB)
  2. Preview video (480p, 15 s, < 5 MB)
  3. Formatted script with metadata
  4. Inline buttons: ✅ Approve | ❌ Reject | 📺 Full Quality
"""

from __future__ import annotations

import logging
import os
import subprocess
from typing import Any, Dict, Optional

logger = logging.getLogger("autofarm.review_gate.telegram_reviewer")


# ---------------------------------------------------------------------------
# TelegramReviewer
# ---------------------------------------------------------------------------


class TelegramReviewer:
    """Send review packages via Telegram with inline buttons.

    Parameters
    ----------
    bot_token:
        Telegram Bot API token.  Falls back to ``TELEGRAM_BOT_TOKEN`` env var.
    chat_id:
        Chat ID to send reviews to.  Falls back to ``TELEGRAM_REVIEW_CHAT_ID`` env var.
    approval_base_url:
        Base URL for the approval server (e.g. ``http://10.0.2.x:8080``).
    """

    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        approval_base_url: Optional[str] = None,
    ) -> None:
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.getenv("TELEGRAM_REVIEW_CHAT_ID", "")
        proxy_ip = os.getenv("PROXY_VM_PUBLIC_IP", "localhost")
        self.approval_base_url = approval_base_url or f"http://{proxy_ip}:8080"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def send_review(
        self,
        review_id: int,
        brand_id: str,
        video_path: str,
        thumbnail_path: str,
        script_text: str,
        review_token: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Send a complete review package via Telegram.

        Parameters
        ----------
        review_id:
            Review primary key.
        brand_id:
            Brand identifier.
        video_path:
            Path to the full-quality video.
        thumbnail_path:
            Path to the thumbnail image.
        script_text:
            Full voiceover script.
        review_token:
            Approval/rejection token.
        metadata:
            Optional metadata dict (duration, hook_type, platforms, etc.).

        Returns
        -------
        bool
            ``True`` if all messages sent successfully.

        Side Effects
        ------------
        Sends 3 messages to the configured Telegram chat:
          1. Thumbnail photo
          2. Preview video (compressed)
          3. Caption with script + inline buttons
        """
        if metadata is None:
            metadata = {}

        try:
            from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

            bot = Bot(token=self.bot_token)

            # 1. Send thumbnail
            if os.path.exists(thumbnail_path):
                with open(thumbnail_path, "rb") as photo:
                    await bot.send_photo(
                        chat_id=self.chat_id,
                        photo=photo,
                        caption=f"📷 Review #{review_id} — {brand_id}",
                    )

            # 2. Send compressed preview video
            preview_path = self._compress_for_telegram(video_path)
            if preview_path and os.path.exists(preview_path):
                with open(preview_path, "rb") as video_file:
                    await bot.send_video(
                        chat_id=self.chat_id,
                        video=video_file,
                        caption=f"🎬 Preview — {brand_id}",
                        supports_streaming=True,
                    )

            # 3. Send script text with inline buttons
            caption = self._format_review_caption(brand_id, script_text, metadata)

            approve_url = f"{self.approval_base_url}/approve/{review_token}"
            reject_url = f"{self.approval_base_url}/reject/{review_token}"
            full_url = f"{self.approval_base_url}/review/{review_token}"

            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "✅ Approve",
                            callback_data=f"approve:{review_token}",
                        ),
                        InlineKeyboardButton(
                            "❌ Reject",
                            callback_data=f"reject:{review_token}",
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            "📺 Full Quality",
                            url=full_url,
                        ),
                    ],
                ]
            )

            await bot.send_message(
                chat_id=self.chat_id,
                text=caption,
                reply_markup=keyboard,
                parse_mode="HTML",
            )

            logger.info(
                "Telegram review sent for review %d (brand=%s)", review_id, brand_id
            )
            return True

        except ImportError:
            logger.error("python-telegram-bot not installed — cannot send review")
            return False
        except Exception as exc:
            logger.error("Telegram send failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Video compression
    # ------------------------------------------------------------------

    def _compress_for_telegram(self, video_path: str) -> Optional[str]:
        """Compress a video to 480p, 15 s, < 5 MB for Telegram.

        Parameters
        ----------
        video_path:
            Source video path.

        Returns
        -------
        Optional[str]
            Path to the compressed preview, or ``None`` on failure.
        """
        preview_path = video_path.replace(".mp4", "_tg_preview.mp4")
        try:
            cmd = [
                "ffmpeg", "-y", "-i", video_path,
                "-t", "15",
                "-vf", "scale=480:-2",
                "-c:v", "libx264", "-crf", "28",
                "-c:a", "aac", "-b:a", "64k",
                "-movflags", "+faststart",
                preview_path,
            ]
            subprocess.run(cmd, check=True, capture_output=True, timeout=60)

            # Ensure under 5 MB
            size_mb = os.path.getsize(preview_path) / (1024 * 1024)
            if size_mb > 5:
                # Re-encode at lower quality
                cmd[-5] = "32"  # Higher CRF
                subprocess.run(cmd, check=True, capture_output=True, timeout=60)

            return preview_path
        except Exception as exc:
            logger.warning("Telegram video compression failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Caption formatting
    # ------------------------------------------------------------------

    @staticmethod
    def _format_review_caption(
        brand_id: str,
        script_text: str,
        metadata: Dict[str, Any],
    ) -> str:
        """Format a readable HTML caption for the Telegram review message.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        script_text:
            Full voiceover script.
        metadata:
            Dict with optional keys: duration, hook_type, platforms,
            word_count, series.

        Returns
        -------
        str
            HTML-formatted caption.
        """
        duration = metadata.get("duration_seconds", "?")
        hook_type = metadata.get("hook_type", "unknown")
        platforms = metadata.get("platforms", [])
        word_count = len(script_text.split())

        platforms_str = ", ".join(platforms) if platforms else "all"

        # Truncate script for Telegram (4096 char limit)
        max_script_len = 2000
        script_display = script_text[:max_script_len]
        if len(script_text) > max_script_len:
            script_display += "… [truncated]"

        caption = (
            f"<b>📝 Content Review — {brand_id}</b>\n\n"
            f"<b>Duration:</b> {duration}s\n"
            f"<b>Words:</b> {word_count}\n"
            f"<b>Hook type:</b> {hook_type}\n"
            f"<b>Platforms:</b> {platforms_str}\n\n"
            f"<b>Script:</b>\n<i>{script_display}</i>\n\n"
            "Use the buttons below to approve or reject."
        )
        return caption
