"""
Telegram Bot — sends system notifications, alerts, daily digests, and
milestone celebrations via Telegram Bot API.

Primary notification channel.  Runs alongside the review bot
(``modules/review_gate/telegram_reviewer.py``) but uses a separate
notification chat.

Configuration via environment variables:
``TELEGRAM_BOT_TOKEN``, ``TELEGRAM_NOTIFY_CHAT_ID``.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("autofarm.notifications.telegram")


class TelegramNotifier:
    """Send notification messages via Telegram Bot API.

    Parameters
    ----------
    db:
        Database helper instance.
    """

    def __init__(self, db: Any) -> None:
        self.db = db
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.getenv("TELEGRAM_NOTIFY_CHAT_ID", "")
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"

    # ------------------------------------------------------------------
    # Core send
    # ------------------------------------------------------------------

    async def send_message(
        self,
        text: str,
        chat_id: Optional[str] = None,
        parse_mode: str = "HTML",
        disable_notification: bool = False,
    ) -> bool:
        """Send a text message via Telegram.

        Parameters
        ----------
        text:
            Message text (HTML or Markdown).
        chat_id:
            Target chat. Defaults to ``TELEGRAM_NOTIFY_CHAT_ID``.
        parse_mode:
            ``HTML`` or ``Markdown``.
        disable_notification:
            If ``True``, sends silently.

        Returns
        -------
        bool
            ``True`` if sent successfully.
        """
        import aiohttp

        target = chat_id or self.chat_id
        if not target or not self.bot_token:
            logger.warning("Telegram not configured for notifications")
            return False

        url = f"{self.base_url}/sendMessage"
        payload = {
            "chat_id": target,
            "text": text[:4096],  # Telegram limit
            "parse_mode": parse_mode,
            "disable_notification": disable_notification,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 200:
                        logger.debug("Telegram message sent to %s", target)
                        return True
                    else:
                        body = await resp.text()
                        logger.error(
                            "Telegram API %d: %s", resp.status, body[:200]
                        )
                        return False

        except Exception as exc:
            logger.error("Telegram send error: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Daily digest
    # ------------------------------------------------------------------

    async def send_daily_digest(self) -> bool:
        """Send a daily summary via Telegram.

        Returns
        -------
        bool
            ``True`` if sent successfully.

        Side Effects
        ------------
        Queries database for yesterday's stats.
        """
        published = await self.db.fetch_all(
            """
            SELECT brand_id, platform, title
            FROM publish_jobs
            WHERE status = 'published'
                  AND DATE(published_at) = DATE('now', '-1 day')
            """
        )

        pending_reviews = await self.db.fetch_one(
            "SELECT COUNT(*) AS cnt FROM reviews WHERE status = 'pending'"
        )

        queue_depth = await self.db.fetch_one(
            "SELECT COUNT(*) AS cnt FROM content_queue WHERE status = 'ready'"
        )

        errors = await self.db.fetch_one(
            """
            SELECT COUNT(*) AS cnt FROM publish_jobs
            WHERE status = 'failed'
                  AND DATE(updated_at) = DATE('now', '-1 day')
            """
        )

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        lines = [
            f"<b>\U0001f4ca Daily Digest — {today}</b>",
            "",
            f"\U00002705 <b>Published:</b> {len(published)} videos",
        ]

        # Top 5 published
        for p in published[:5]:
            name = p["brand_id"].replace("_success_guru", "").title()
            icon = {"tiktok": "\U0001f3b5", "instagram": "\U0001f4f7",
                    "facebook": "\U0001f310", "youtube": "\U0001f3ac",
                    "snapchat": "\U0001f47b"}.get(p["platform"], "")
            title = (p.get("title") or "Untitled")[:30]
            lines.append(f"  {icon} {name}/{p['platform']}: {title}")

        if len(published) > 5:
            lines.append(f"  ... and {len(published) - 5} more")

        lines.extend([
            "",
            f"\U0001f4e6 <b>Queue:</b> {queue_depth['cnt'] if queue_depth else 0} ready",
            f"\U0001f50d <b>Pending Reviews:</b> {pending_reviews['cnt'] if pending_reviews else 0}",
        ])

        err_count = errors["cnt"] if errors else 0
        if err_count:
            lines.append(f"\u26a0\ufe0f <b>Errors:</b> {err_count}")

        text = "\n".join(lines)
        success = await self.send_message(text)

        await self._log_notification("daily_digest", text, success)
        return success

    # ------------------------------------------------------------------
    # Error alert
    # ------------------------------------------------------------------

    async def send_error_alert(
        self,
        error_type: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Send an urgent error alert via Telegram.

        Parameters
        ----------
        error_type:
            Category of error.
        message:
            Human-readable description.
        details:
            Optional context dict.

        Returns
        -------
        bool
            ``True`` if sent successfully.
        """
        now = datetime.now(timezone.utc).strftime("%H:%M UTC")
        lines = [
            f"\U0001f6a8 <b>ALERT: {error_type}</b>",
            f"\U0001f552 {now}",
            "",
            message,
        ]

        if details:
            for k, v in details.items():
                lines.append(f"<b>{k}:</b> {v}")

        text = "\n".join(lines)
        success = await self.send_message(text)
        await self._log_notification("error_alert", text, success)
        return success

    # ------------------------------------------------------------------
    # Milestone
    # ------------------------------------------------------------------

    async def send_milestone_notification(
        self,
        brand_id: str,
        platform: str,
        milestone: str,
        follower_count: int,
    ) -> bool:
        """Send a milestone celebration message.

        Parameters
        ----------
        brand_id:
            Brand that achieved the milestone.
        platform:
            Platform name.
        milestone:
            Milestone label (e.g. "10K").
        follower_count:
            Actual follower count.

        Returns
        -------
        bool
            ``True`` if sent successfully.
        """
        name = brand_id.replace("_success_guru", "").replace("_", " ").title()
        icon = {"tiktok": "\U0001f3b5", "instagram": "\U0001f4f7",
                "facebook": "\U0001f310", "youtube": "\U0001f3ac",
                "snapchat": "\U0001f47b"}.get(platform, "\U0001f389")

        text = (
            f"\U0001f389\U0001f389\U0001f389\n\n"
            f"<b>{name} Success Guru</b> just hit "
            f"<b>{milestone} followers</b> on {icon} {platform.title()}!\n\n"
            f"Current count: <b>{follower_count:,}</b>\n\n"
            f"\U0001f680 Keep going!"
        )

        success = await self.send_message(text)
        await self._log_notification("milestone", text, success)
        return success

    # ------------------------------------------------------------------
    # Publish confirmation
    # ------------------------------------------------------------------

    async def send_publish_confirmation(
        self,
        brand_id: str,
        platform: str,
        title: str,
        platform_url: Optional[str] = None,
    ) -> bool:
        """Notify that a video was successfully published.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        platform:
            Platform name.
        title:
            Video title or hook.
        platform_url:
            Direct URL to the published video.

        Returns
        -------
        bool
            ``True`` if sent successfully.
        """
        name = brand_id.replace("_success_guru", "").replace("_", " ").title()
        icon = {"tiktok": "\U0001f3b5", "instagram": "\U0001f4f7",
                "facebook": "\U0001f310", "youtube": "\U0001f3ac",
                "snapchat": "\U0001f47b"}.get(platform, "")

        lines = [
            f"\u2705 <b>Published!</b>",
            f"{icon} {name} → {platform.title()}",
            f"<i>{title[:60]}</i>",
        ]
        if platform_url:
            lines.append(f"\U0001f517 {platform_url}")

        text = "\n".join(lines)
        return await self.send_message(text, disable_notification=True)

    # ------------------------------------------------------------------
    # Health warning
    # ------------------------------------------------------------------

    async def send_health_warning(
        self,
        component: str,
        status: str,
        message: str,
    ) -> bool:
        """Send a health-check warning message.

        Parameters
        ----------
        component:
            System component name.
        status:
            Health status (warning/critical).
        message:
            Description of the issue.

        Returns
        -------
        bool
            ``True`` if sent successfully.
        """
        emoji = "\u26a0\ufe0f" if status == "warning" else "\U0001f6a8"
        text = (
            f"{emoji} <b>Health {status.upper()}: {component}</b>\n\n"
            f"{message}"
        )

        success = await self.send_message(text)
        await self._log_notification("health_warning", text, success)
        return success

    # ------------------------------------------------------------------
    # A/B test result
    # ------------------------------------------------------------------

    async def send_ab_test_result(
        self,
        brand_id: str,
        platform: str,
        hook_type_a: str,
        hook_type_b: str,
        winner: str,
        margin: float,
    ) -> bool:
        """Notify about an A/B test result.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        platform:
            Platform name.
        hook_type_a:
            Hook type for variant A.
        hook_type_b:
            Hook type for variant B.
        winner:
            ``'A'`` or ``'B'``.
        margin:
            Victory margin.

        Returns
        -------
        bool
            ``True`` if sent successfully.
        """
        name = brand_id.replace("_success_guru", "").replace("_", " ").title()
        winner_hook = hook_type_a if winner == "A" else hook_type_b
        loser_hook = hook_type_b if winner == "A" else hook_type_a

        text = (
            f"\U0001f9ea <b>A/B Test Result</b>\n"
            f"{name} / {platform.title()}\n\n"
            f"\U0001f947 Winner: <b>{winner_hook}</b>\n"
            f"\U0001f948 Loser: {loser_hook}\n"
            f"Margin: {margin:.4f}"
        )

        return await self.send_message(text, disable_notification=True)

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    async def _log_notification(
        self, notification_type: str, message: str, success: bool
    ) -> None:
        """Log notification to database.

        Parameters
        ----------
        notification_type:
            Type of notification.
        message:
            Message content.
        success:
            Delivery success flag.

        Side Effects
        ------------
        Inserts row into ``notifications`` table.
        """
        now = datetime.now(timezone.utc).isoformat()
        try:
            await self.db.execute(
                """
                INSERT INTO notifications
                    (type, channel, message, status, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    notification_type,
                    "telegram",
                    message[:500],
                    "sent" if success else "failed",
                    now,
                ),
            )
        except Exception as exc:
            logger.error("Failed to log notification: %s", exc)


if __name__ == "__main__":
    import asyncio
    import time
    from database.db import Database

    db = Database()
    notifier = TelegramNotifier(db=db)

    async def run_bot():
        """Run the Telegram notifier as a long-running daemon."""
        logger.info("Telegram notifier daemon started")
        while True:
            try:
                await notifier.send_daily_digest()
            except Exception as e:
                logger.error("Telegram daemon error: %s", e)
            await asyncio.sleep(3600)  # Check every hour

    asyncio.run(run_bot())
