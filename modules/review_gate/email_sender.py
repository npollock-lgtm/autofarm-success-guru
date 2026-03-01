"""
Review Email Sender — fallback review channel using SMTP + optional Google Drive.

Sends rich HTML emails with:
  1. Embedded thumbnail (base64 inline)
  2. Google Drive video link (if enabled)
  3. Full script text
  4. Metrics: duration, word count, hook type, platforms
  5. Approve / Reject CTA buttons
  6. Expiry notices
"""

from __future__ import annotations

import base64
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("autofarm.review_gate.email_sender")


# ---------------------------------------------------------------------------
# ReviewEmailSender
# ---------------------------------------------------------------------------


class ReviewEmailSender:
    """Send review emails as fallback when Telegram is unavailable.

    Parameters
    ----------
    smtp_host:
        SMTP server hostname.
    smtp_port:
        SMTP port.
    smtp_user:
        SMTP username.
    smtp_password:
        SMTP password.
    smtp_use_tls:
        Whether to use TLS.
    reviewer_email:
        Email address of the reviewer.
    from_email:
        Sender email address.
    approval_base_url:
        Base URL for the approval server.
    gdrive_uploader:
        Optional ``GDriveVideoUploader`` instance.
    """

    def __init__(
        self,
        smtp_host: Optional[str] = None,
        smtp_port: int = 587,
        smtp_user: Optional[str] = None,
        smtp_password: Optional[str] = None,
        smtp_use_tls: bool = True,
        reviewer_email: Optional[str] = None,
        from_email: Optional[str] = None,
        approval_base_url: Optional[str] = None,
        gdrive_uploader: Optional[Any] = None,
    ) -> None:
        self.smtp_host = smtp_host or os.getenv("SMTP_HOST", "smtp.gmail.com")
        self.smtp_port = int(os.getenv("SMTP_PORT", str(smtp_port)))
        self.smtp_user = smtp_user or os.getenv("SMTP_USER", "")
        self.smtp_password = smtp_password or os.getenv("SMTP_PASSWORD", "")
        self.smtp_use_tls = smtp_use_tls
        self.reviewer_email = reviewer_email or os.getenv("REVIEWER_EMAIL", "")
        self.from_email = from_email or os.getenv("SMTP_USER", "")
        proxy_ip = os.getenv("PROXY_VM_PUBLIC_IP", "localhost")
        self.approval_base_url = approval_base_url or f"http://{proxy_ip}:8080"
        self.gdrive = gdrive_uploader

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def send_review_email(
        self,
        review_id: int,
        brand_id: str,
        video_path: str,
        thumbnail_path: str,
        script_text: str,
        review_token: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Send a review email with embedded thumbnail and approval links.

        Parameters
        ----------
        review_id:
            Review primary key.
        brand_id:
            Brand identifier.
        video_path:
            Path to the video file.
        thumbnail_path:
            Path to the thumbnail image.
        script_text:
            Full voiceover script.
        review_token:
            Approval token.
        metadata:
            Optional metadata dict.

        Returns
        -------
        bool
            ``True`` if email sent successfully.

        Side Effects
        ------------
        * Optionally uploads video to Google Drive.
        * Sends an email via SMTP.
        """
        if metadata is None:
            metadata = {}

        # Upload to Google Drive if available
        gdrive_url = ""
        if self.gdrive and os.getenv("GDRIVE_ENABLED", "false").lower() == "true":
            try:
                file_id, preview_url = await self._upload_to_gdrive_and_get_url(
                    video_path, thumbnail_path, review_token, brand_id
                )
                gdrive_url = preview_url
            except Exception as exc:
                logger.warning("Google Drive upload failed: %s", exc)

        # Build email
        html = self._generate_review_email_html(
            review_id=review_id,
            brand_id=brand_id,
            script_text=script_text,
            review_token=review_token,
            gdrive_url=gdrive_url,
            thumbnail_path=thumbnail_path,
            metadata=metadata,
        )

        try:
            msg = MIMEMultipart("related")
            msg["Subject"] = f"[AutoFarm] Review #{review_id} — {brand_id}"
            msg["From"] = self.from_email
            msg["To"] = self.reviewer_email

            # HTML body
            html_part = MIMEText(html, "html")
            msg.attach(html_part)

            # Embed thumbnail
            if os.path.exists(thumbnail_path):
                with open(thumbnail_path, "rb") as f:
                    img_data = f.read()
                img = MIMEImage(img_data)
                img.add_header("Content-ID", "<thumbnail>")
                img.add_header(
                    "Content-Disposition", "inline", filename="thumbnail.jpg"
                )
                msg.attach(img)

            # Send
            self._send_smtp(msg)
            logger.info(
                "Review email sent for review %d (brand=%s)", review_id, brand_id
            )
            return True

        except Exception as exc:
            logger.error("Email send failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Google Drive upload
    # ------------------------------------------------------------------

    async def _upload_to_gdrive_and_get_url(
        self,
        video_path: str,
        thumbnail_path: str,
        review_token: str,
        brand_id: str,
    ) -> tuple:
        """Upload video + thumbnail to Google Drive.

        Parameters
        ----------
        video_path:
            Path to video file.
        thumbnail_path:
            Path to thumbnail.
        review_token:
            Review token (used as filename prefix).
        brand_id:
            Brand identifier.

        Returns
        -------
        tuple[str, str]
            ``(file_id, preview_url)``.
        """
        if not self.gdrive:
            return ("", "")

        file_id, preview_url = await self.gdrive.upload_video(
            video_path=video_path,
            review_token=review_token,
            brand_id=brand_id,
        )
        # Also upload thumbnail
        await self.gdrive.upload_thumbnail(
            thumbnail_path=thumbnail_path,
            review_token=review_token,
        )
        return (file_id, preview_url)

    # ------------------------------------------------------------------
    # HTML generation
    # ------------------------------------------------------------------

    def _generate_review_email_html(
        self,
        review_id: int,
        brand_id: str,
        script_text: str,
        review_token: str,
        gdrive_url: str,
        thumbnail_path: str,
        metadata: Dict[str, Any],
    ) -> str:
        """Generate the HTML body for the review email.

        Parameters
        ----------
        review_id:
            Review PK.
        brand_id:
            Brand identifier.
        script_text:
            Script body.
        review_token:
            Approval token.
        gdrive_url:
            Google Drive preview URL (may be empty).
        thumbnail_path:
            Path to thumbnail (embedded via CID).
        metadata:
            Extra metadata dict.

        Returns
        -------
        str
            Full HTML email body.
        """
        approve_url = f"{self.approval_base_url}/approve/{review_token}"
        reject_url = f"{self.approval_base_url}/reject/{review_token}"
        full_review_url = f"{self.approval_base_url}/review/{review_token}"

        duration = metadata.get("duration_seconds", "?")
        word_count = len(script_text.split())
        hook_type = metadata.get("hook_type", "unknown")
        platforms = ", ".join(metadata.get("platforms", ["all"]))

        video_section = ""
        if gdrive_url:
            video_section = f"""
            <div style="text-align:center; margin:20px 0;">
                <a href="{gdrive_url}"
                   style="background:#1a73e8; color:white; padding:12px 24px;
                          text-decoration:none; border-radius:4px; font-size:16px;">
                    ▶ Watch Full Video
                </a>
                <p style="color:#666; font-size:12px; margin-top:8px;">
                    Video link expires in 14 days
                </p>
            </div>
            """

        html = f"""
        <html>
        <body style="font-family:Arial, sans-serif; max-width:600px; margin:0 auto; padding:20px;">
            <h2 style="color:#333;">Content Review #{review_id}</h2>
            <p><strong>Brand:</strong> {brand_id}</p>

            <div style="text-align:center; margin:20px 0;">
                <img src="cid:thumbnail" alt="Thumbnail"
                     style="max-width:300px; border-radius:8px; border:1px solid #ddd;" />
            </div>

            {video_section}

            <div style="background:#f9f9f9; padding:15px; border-radius:8px; margin:20px 0;">
                <h3 style="margin-top:0;">Metrics</h3>
                <p><strong>Duration:</strong> {duration}s</p>
                <p><strong>Word count:</strong> {word_count}</p>
                <p><strong>Hook type:</strong> {hook_type}</p>
                <p><strong>Platforms:</strong> {platforms}</p>
            </div>

            <div style="background:#f0f0f0; padding:15px; border-radius:8px; margin:20px 0;">
                <h3 style="margin-top:0;">Script</h3>
                <p style="white-space:pre-wrap; font-size:14px; line-height:1.6;">
                    {script_text}
                </p>
            </div>

            <div style="text-align:center; margin:30px 0;">
                <a href="{approve_url}"
                   style="background:#28a745; color:white; padding:14px 40px;
                          text-decoration:none; border-radius:6px; font-size:18px;
                          margin-right:20px;">
                    ✅ APPROVE
                </a>
                <a href="{reject_url}"
                   style="background:#dc3545; color:white; padding:14px 40px;
                          text-decoration:none; border-radius:6px; font-size:18px;">
                    ❌ REJECT
                </a>
            </div>

            <hr style="border:none; border-top:1px solid #eee; margin:20px 0;" />
            <p style="color:#999; font-size:11px; text-align:center;">
                AutoFarm V6 Review System &bull;
                <a href="{full_review_url}">View full review page</a>
            </p>
        </body>
        </html>
        """
        return html

    # ------------------------------------------------------------------
    # SMTP helper
    # ------------------------------------------------------------------

    def _send_smtp(self, msg: MIMEMultipart) -> None:
        """Send an email via SMTP.

        Parameters
        ----------
        msg:
            Constructed email message.

        Raises
        ------
        smtplib.SMTPException
            On SMTP failure.
        """
        if self.smtp_use_tls:
            server = smtplib.SMTP(self.smtp_host, self.smtp_port)
            server.starttls()
        else:
            server = smtplib.SMTP(self.smtp_host, self.smtp_port)

        if self.smtp_user and self.smtp_password:
            server.login(self.smtp_user, self.smtp_password)

        server.send_message(msg)
        server.quit()
