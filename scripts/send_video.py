"""
Send a generated video via Telegram (primary) with Gmail email fallback.

Designed to run on the proxy VM which has the direct public internet
connection for faster uploads. The content VM forwards videos here via SCP.

Usage:
    python send_video.py /path/to/video.mp4 brand_id "caption text"
    python send_video.py /path/to/video.mp4 brand_id "caption text" --email-to user@example.com

Flow:
    1. Compress video if > 45MB (Telegram limit is 50MB)
    2. Try Telegram upload
    3. If Telegram fails → send email with video attached (< 20MB) or
       email notification with file location (> 20MB)
"""

import argparse
import os
import smtplib
import subprocess
import sys
from datetime import datetime, timezone
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders

# Load env
from dotenv import load_dotenv
load_dotenv("/app/.env")
# Also try proxy VM location
if os.path.exists("/home/opc/.env"):
    load_dotenv("/home/opc/.env", override=True)


# ---------------------------------------------------------------------------
# Telegram sender
# ---------------------------------------------------------------------------

def compress_video(video_path: str, max_size_mb: float = 45.0) -> str:
    """Compress video to fit within size limit. Returns path to use."""
    size_mb = os.path.getsize(video_path) / (1024 * 1024)
    if size_mb <= max_size_mb:
        return video_path

    print(f"  [compress] {size_mb:.0f}MB > {max_size_mb:.0f}MB, compressing to 720p...")
    compressed = video_path.replace(".mp4", "_compressed.mp4")
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vf", "scale=720:-2",
        "-c:v", "libx264", "-crf", "30", "-preset", "fast",
        "-c:a", "aac", "-b:a", "96k",
        "-movflags", "+faststart",
        compressed,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)
        new_size = os.path.getsize(compressed) / (1024 * 1024)
        print(f"  [compress] Done: {new_size:.1f}MB")
        return compressed
    except Exception as e:
        print(f"  [compress] Failed: {e}")
        return video_path


def send_telegram(video_path: str, caption: str) -> bool:
    """Send video via Telegram Bot API.

    Parameters
    ----------
    video_path : str
        Path to video file.
    caption : str
        Message caption (max 1024 chars).

    Returns
    -------
    bool
        True if sent successfully.
    """
    import requests

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_REVIEW_CHAT_ID", "")

    if not bot_token or not chat_id:
        print("  [telegram] Not configured (missing BOT_TOKEN or CHAT_ID)")
        return False

    # Compress if needed
    send_path = compress_video(video_path)
    size_mb = os.path.getsize(send_path) / (1024 * 1024)

    if size_mb > 50:
        print(f"  [telegram] File still too large after compression ({size_mb:.0f}MB > 50MB)")
        return False

    print(f"  [telegram] Uploading {os.path.basename(send_path)} ({size_mb:.1f}MB)...")
    url = f"https://api.telegram.org/bot{bot_token}/sendVideo"

    try:
        with open(send_path, "rb") as vf:
            resp = requests.post(
                url,
                data={
                    "chat_id": chat_id,
                    "caption": caption[:1024],
                    "parse_mode": "HTML",
                },
                files={"video": (os.path.basename(send_path), vf, "video/mp4")},
                timeout=300,  # 5 minutes — proxy has faster upload
            )

        if resp.status_code == 200:
            print("  [telegram] Sent successfully")
            return True
        else:
            print(f"  [telegram] API error {resp.status_code}: {resp.text[:300]}")
            return False

    except Exception as e:
        print(f"  [telegram] Upload failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Gmail email sender (fallback)
# ---------------------------------------------------------------------------

def send_email(
    video_path: str,
    brand_id: str,
    caption: str,
    email_to: str = "",
) -> bool:
    """Send video notification via Gmail SMTP.

    If the video is under 20MB, attaches it to the email.
    Otherwise sends a notification with the file location.

    Parameters
    ----------
    video_path : str
        Path to video file.
    brand_id : str
        Brand identifier.
    caption : str
        Script text / caption.
    email_to : str
        Recipient email. Defaults to SMTP_USER (send to self).

    Returns
    -------
    bool
        True if sent successfully.
    """
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASSWORD", "") or os.getenv("SMTP_PASS", "")
    from_name = os.getenv("SMTP_FROM_NAME", "AutoFarm V6")
    recipient = email_to or os.getenv("NOTIFY_EMAIL_TO", "") or smtp_user

    if not smtp_user or not smtp_pass:
        print("  [email] SMTP credentials not configured")
        return False

    if not recipient:
        print("  [email] No recipient email configured")
        return False

    size_mb = os.path.getsize(video_path) / (1024 * 1024)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    brand_name = brand_id.replace("_success_guru", "").replace("_", " ").title()

    # Build email
    msg = MIMEMultipart()
    msg["Subject"] = f"AutoFarm Video: {brand_name} Success Guru — {now}"
    msg["From"] = f"{from_name} <{smtp_user}>"
    msg["To"] = recipient

    # HTML body
    html = f"""
    <h2>New Video Generated</h2>
    <table style="border-collapse:collapse; border:1px solid #ccc; padding:8px;">
      <tr><td><strong>Brand:</strong></td><td>{brand_name} Success Guru</td></tr>
      <tr><td><strong>Generated:</strong></td><td>{now}</td></tr>
      <tr><td><strong>File Size:</strong></td><td>{size_mb:.1f} MB</td></tr>
      <tr><td><strong>File:</strong></td><td>{os.path.basename(video_path)}</td></tr>
    </table>
    <h3>Script:</h3>
    <p style="background:#f5f5f5; padding:12px; border-radius:4px; font-size:14px;">
      {caption[:2000].replace(chr(10), '<br>')}
    </p>
    """

    if size_mb <= 20:
        html += "<p><strong>Video attached below.</strong></p>"
    else:
        html += f"""
        <p><strong>Video too large to attach ({size_mb:.0f}MB).</strong></p>
        <p>Retrieve it with SCP:</p>
        <pre style="background:#222; color:#0f0; padding:10px; border-radius:4px;">
scp opc@150.230.172.213:{video_path} ~/Downloads/
        </pre>
        """

    msg.attach(MIMEText(html, "html"))

    # Attach video if small enough (Gmail limit ~25MB, keep under 20 to be safe)
    if size_mb <= 20:
        print(f"  [email] Attaching video ({size_mb:.1f}MB)...")
        try:
            with open(video_path, "rb") as vf:
                part = MIMEBase("video", "mp4")
                part.set_payload(vf.read())
                encoders.encode_base64(part)
                part.add_header(
                    "Content-Disposition",
                    f'attachment; filename="{os.path.basename(video_path)}"',
                )
                msg.attach(part)
        except Exception as e:
            print(f"  [email] Failed to attach video: {e}")

    # Send
    print(f"  [email] Sending to {recipient}...")
    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=60) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, [recipient], msg.as_string())

        print("  [email] Sent successfully")
        return True

    except smtplib.SMTPAuthenticationError as e:
        print(f"  [email] Authentication failed: {e}")
        return False
    except smtplib.SMTPException as e:
        print(f"  [email] SMTP error: {e}")
        return False
    except Exception as e:
        print(f"  [email] Error: {e}")
        return False


# ---------------------------------------------------------------------------
# Main — Telegram first, email fallback
# ---------------------------------------------------------------------------

def send_video(
    video_path: str,
    brand_id: str,
    caption: str,
    email_to: str = "",
) -> bool:
    """Send video via Telegram (primary) with Gmail fallback.

    Parameters
    ----------
    video_path : str
        Path to video file.
    brand_id : str
        Brand identifier for display.
    caption : str
        Script text to include in notification.
    email_to : str
        Optional email recipient override.

    Returns
    -------
    bool
        True if sent via either channel.
    """
    if not os.path.exists(video_path):
        print(f"  [ERROR] Video not found: {video_path}")
        return False

    size_mb = os.path.getsize(video_path) / (1024 * 1024)
    print(f"\n  Sending: {os.path.basename(video_path)} ({size_mb:.1f}MB)")
    print(f"  Brand: {brand_id}")

    # Try Telegram first
    print("\n  --- Trying Telegram (primary) ---")
    if send_telegram(video_path, f"<b>{brand_id}</b>\n\n{caption[:900]}"):
        return True

    # Fallback to email
    print("\n  --- Telegram failed, trying Gmail (fallback) ---")
    if send_email(video_path, brand_id, caption, email_to):
        return True

    print("\n  [FAIL] Both Telegram and email failed")
    return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Send generated video via Telegram + Gmail fallback"
    )
    parser.add_argument("video_path", help="Path to video file")
    parser.add_argument("brand_id", help="Brand identifier")
    parser.add_argument("caption", help="Script text / caption")
    parser.add_argument(
        "--email-to", default="",
        help="Override email recipient (default: SMTP_USER)"
    )
    args = parser.parse_args()

    success = send_video(args.video_path, args.brand_id, args.caption, args.email_to)
    sys.exit(0 if success else 1)
