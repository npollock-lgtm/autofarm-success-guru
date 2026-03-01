"""
Email Notifier — sends system notification emails for daily digests,
error alerts, milestone celebrations, and weekly reports.

Uses SMTP with TLS.  Configuration via environment variables:
``SMTP_HOST``, ``SMTP_PORT``, ``SMTP_USER``, ``SMTP_PASS``,
``NOTIFY_EMAIL_FROM``, ``NOTIFY_EMAIL_TO``.
"""

from __future__ import annotations

import logging
import os
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional

logger = logging.getLogger("autofarm.notifications.email")


class EmailNotifier:
    """Send notification emails via SMTP.

    Parameters
    ----------
    db:
        Database helper instance.
    """

    def __init__(self, db: Any) -> None:
        self.db = db
        self.smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_user = os.getenv("SMTP_USER", "")
        self.smtp_pass = os.getenv("SMTP_PASS", "")
        self.from_addr = os.getenv("NOTIFY_EMAIL_FROM", self.smtp_user)
        self.to_addr = os.getenv("NOTIFY_EMAIL_TO", "")

    # ------------------------------------------------------------------
    # Send email
    # ------------------------------------------------------------------

    def send_email(
        self,
        subject: str,
        html_body: str,
        to_addr: Optional[str] = None,
    ) -> bool:
        """Send an HTML email.

        Parameters
        ----------
        subject:
            Email subject line.
        html_body:
            HTML content string.
        to_addr:
            Recipient address.  Defaults to ``NOTIFY_EMAIL_TO``.

        Returns
        -------
        bool
            ``True`` if sent successfully.

        Side Effects
        ------------
        Sends email via SMTP.
        Logs row to ``notifications`` table.
        """
        recipient = to_addr or self.to_addr
        if not recipient:
            logger.warning("No recipient configured for email notifications")
            return False

        if not self.smtp_user or not self.smtp_pass:
            logger.warning("SMTP credentials not configured")
            return False

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.from_addr
        msg["To"] = recipient

        # Plain text fallback
        plain = html_body.replace("<br>", "\n").replace("</p>", "\n")
        import re
        plain = re.sub(r"<[^>]+>", "", plain)

        msg.attach(MIMEText(plain, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30) as server:
                server.ehlo()
                server.starttls()
                server.login(self.smtp_user, self.smtp_pass)
                server.sendmail(self.from_addr, [recipient], msg.as_string())

            logger.info("Email sent: %s -> %s", subject, recipient)
            return True

        except smtplib.SMTPException as exc:
            logger.error("SMTP error: %s", exc)
            return False
        except Exception as exc:
            logger.error("Email error: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Daily digest
    # ------------------------------------------------------------------

    async def send_daily_digest(self) -> bool:
        """Send a daily summary of system activity.

        Returns
        -------
        bool
            ``True`` if sent successfully.

        Side Effects
        ------------
        Queries database for yesterday's stats and sends digest email.
        """
        # Published yesterday
        published = await self.db.fetch_all(
            """
            SELECT brand_id, platform, title, platform_url
            FROM publish_jobs
            WHERE status = 'published'
                  AND DATE(published_at) = DATE('now', '-1 day')
            ORDER BY published_at
            """
        )

        # Pending reviews
        pending_reviews = await self.db.fetch_one(
            "SELECT COUNT(*) AS cnt FROM reviews WHERE status = 'pending'"
        )

        # Queue depth
        queue_depth = await self.db.fetch_one(
            "SELECT COUNT(*) AS cnt FROM content_queue WHERE status = 'ready'"
        )

        # Errors
        errors = await self.db.fetch_all(
            """
            SELECT brand_id, platform, error_message
            FROM publish_jobs
            WHERE status = 'failed'
                  AND DATE(updated_at) = DATE('now', '-1 day')
            LIMIT 10
            """
        )

        # Build HTML
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        pub_html = ""
        for p in published:
            pub_html += (
                f"<tr><td>{p['brand_id']}</td><td>{p['platform']}</td>"
                f"<td>{p.get('title', '—')}</td></tr>"
            )

        err_html = ""
        for e in errors:
            err_html += (
                f"<tr><td>{e['brand_id']}</td><td>{e['platform']}</td>"
                f"<td>{(e.get('error_message','') or '')[:80]}</td></tr>"
            )

        html = f"""
        <h2>AutoFarm Daily Digest — {today}</h2>
        <h3>Published ({len(published)} videos)</h3>
        <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse">
          <tr><th>Brand</th><th>Platform</th><th>Title</th></tr>
          {pub_html if pub_html else '<tr><td colspan="3">No videos published</td></tr>'}
        </table>
        <h3>Queue: {queue_depth['cnt'] if queue_depth else 0} ready</h3>
        <h3>Pending Reviews: {pending_reviews['cnt'] if pending_reviews else 0}</h3>
        {f'<h3>Errors ({len(errors)})</h3><table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse"><tr><th>Brand</th><th>Platform</th><th>Error</th></tr>{err_html}</table>' if errors else ''}
        <p><a href="http://{os.getenv("PROXY_VM_PUBLIC_IP", "localhost")}:8080/dashboard">Open Dashboard</a></p>
        """

        return self.send_email(f"AutoFarm Daily Digest — {today}", html)

    # ------------------------------------------------------------------
    # Error alert
    # ------------------------------------------------------------------

    async def send_error_alert(
        self,
        error_type: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Send an urgent error alert email.

        Parameters
        ----------
        error_type:
            Category of error (e.g. "publish_failure", "rate_limit").
        message:
            Human-readable error description.
        details:
            Optional extra context.

        Returns
        -------
        bool
            ``True`` if sent successfully.
        """
        now = datetime.now(timezone.utc).isoformat()
        detail_html = ""
        if details:
            for k, v in details.items():
                detail_html += f"<tr><td><strong>{k}</strong></td><td>{v}</td></tr>"

        html = f"""
        <h2 style="color:red">\u26a0\ufe0f AutoFarm Error Alert</h2>
        <p><strong>Type:</strong> {error_type}</p>
        <p><strong>Time:</strong> {now}</p>
        <p><strong>Message:</strong> {message}</p>
        {f'<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse">{detail_html}</table>' if detail_html else ''}
        <p><a href="http://{os.getenv("PROXY_VM_PUBLIC_IP", "localhost")}:8080/dashboard/health">Check Health Dashboard</a></p>
        """

        return self.send_email(f"\u26a0\ufe0f AutoFarm Alert: {error_type}", html)

    # ------------------------------------------------------------------
    # Milestone notification
    # ------------------------------------------------------------------

    async def send_milestone_notification(
        self,
        brand_id: str,
        platform: str,
        milestone: str,
        follower_count: int,
    ) -> bool:
        """Send a milestone celebration email.

        Parameters
        ----------
        brand_id:
            Brand that hit the milestone.
        platform:
            Platform where milestone was reached.
        milestone:
            Milestone label (e.g. "1K", "10K").
        follower_count:
            Actual follower count.

        Returns
        -------
        bool
            ``True`` if sent successfully.
        """
        name = brand_id.replace("_success_guru", "").replace("_", " ").title()

        html = f"""
        <h2>\U0001f389 Milestone Reached!</h2>
        <p><strong>{name} Success Guru</strong> just hit <strong>{milestone} followers</strong>
           on {platform.title()}!</p>
        <p>Current count: {follower_count:,}</p>
        <p><a href="http://{os.getenv("PROXY_VM_PUBLIC_IP", "localhost")}:8080/dashboard/brand/{brand_id}">View Brand Dashboard</a></p>
        """

        return self.send_email(
            f"\U0001f389 {name}: {milestone} followers on {platform.title()}!",
            html,
        )

    # ------------------------------------------------------------------
    # Weekly report
    # ------------------------------------------------------------------

    async def send_weekly_report(self) -> bool:
        """Send a weekly performance summary.

        Returns
        -------
        bool
            ``True`` if sent successfully.

        Side Effects
        ------------
        Queries 7-day analytics and sends formatted report.
        """
        brand_stats = await self.db.fetch_all(
            """
            SELECT brand_id,
                   COUNT(*) AS videos,
                   AVG(cps_score) AS avg_cps,
                   SUM(views) AS total_views,
                   AVG(engagement_rate) AS avg_eng
            FROM analytics
            WHERE pulled_at >= datetime('now', '-7 days')
            GROUP BY brand_id
            ORDER BY avg_cps DESC
            """
        )

        rows_html = ""
        for bs in brand_stats:
            name = bs["brand_id"].replace("_success_guru", "").replace("_", " ").title()
            rows_html += (
                f"<tr><td>{name}</td>"
                f"<td>{bs['videos']}</td>"
                f"<td>{bs['avg_cps']:.2f}</td>"
                f"<td>{int(bs['total_views'] or 0):,}</td>"
                f"<td>{(bs['avg_eng'] or 0)*100:.2f}%</td></tr>"
            )

        html = f"""
        <h2>AutoFarm Weekly Report</h2>
        <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse">
          <tr><th>Brand</th><th>Videos</th><th>Avg CPS</th><th>Total Views</th><th>Avg Engagement</th></tr>
          {rows_html if rows_html else '<tr><td colspan="5">No data this week</td></tr>'}
        </table>
        <p><a href="http://{os.getenv("PROXY_VM_PUBLIC_IP", "localhost")}:8080/dashboard/analytics">Full Analytics</a></p>
        """

        return self.send_email("AutoFarm Weekly Report", html)

    # ------------------------------------------------------------------
    # Log notification
    # ------------------------------------------------------------------

    async def _log_notification(
        self,
        notification_type: str,
        channel: str,
        message: str,
        success: bool,
    ) -> None:
        """Log a notification to the database.

        Parameters
        ----------
        notification_type:
            Type of notification.
        channel:
            Delivery channel (email/telegram).
        message:
            Message summary.
        success:
            Whether delivery succeeded.

        Side Effects
        ------------
        Inserts row into ``notifications`` table.
        """
        now = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            """
            INSERT INTO notifications
                (type, channel, message, status, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                notification_type,
                channel,
                message[:500],
                "sent" if success else "failed",
                now,
            ),
        )
