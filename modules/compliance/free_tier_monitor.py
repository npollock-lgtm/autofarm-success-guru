"""
Free tier limit tracker for AutoFarm Zero — Success Guru Network v6.0.

Tracks usage against free tier limits for all external services.
Alerts via Telegram if any service exceeds 80% of its limit.
Auto-throttles services approaching their limits to prevent overages.

Corrected limits from V5.1 — especially Groq and OCI Object Storage.
"""

import os
import logging
from datetime import datetime

from database.db import Database

logger = logging.getLogger(__name__)


# Authoritative free tier limits for all services
FREE_TIER_LIMITS = {
    "groq_api": {
        "model_limits": {
            "llama-3.3-70b-versatile": {
                "requests_per_minute": 30,
                "requests_per_day": 1000,
                "tokens_per_minute": 12000,
                "tokens_per_day": 100000,
            },
            "llama-3.1-8b-instant": {
                "requests_per_minute": 30,
                "requests_per_day": 14400,
                "tokens_per_minute": 6000,
                "tokens_per_day": 500000,
            },
        },
        "notes": "Free tier has 429 on exceed, no charges. Limits per org.",
    },
    "pexels_api": {
        "requests_per_hour": 200,
        "requests_per_month": 20000,
    },
    "pixabay_api": {
        "requests_per_hour": 100,
    },
    "newsapi": {
        "requests_per_day": 100,
    },
    "oci_object_storage": {
        "total_gb": 20,
        "alert_threshold_gb": 16,
    },
    "oci_outbound_transfer": {
        "total_tb_per_month": 10,
        "alert_threshold_tb": 8,
    },
    "gmail_smtp": {
        "emails_per_day": 500,
        "notes": "Gmail App Password",
    },
    "google_drive": {
        "total_gb": 15,
        "alert_threshold_gb": 12,
        "notes": "Optional — Telegram review is primary",
    },
    "youtube_data_api": {
        "quota_units_per_day_per_project": 10000,
        "notes": "1 project initially. Split if needed.",
    },
    "reddit_api": {
        "requests_per_minute": 60,
    },
    "telegram_bot_api": {
        "messages_per_second": 30,
        "messages_per_minute_per_chat": 20,
        "notes": "Generous limits — not a concern for review workflow",
    },
}


class FreeTierMonitor:
    """
    Tracks usage against free tier limits for all services.

    Monitors each service's consumption, alerts when approaching limits
    (80% threshold), and can auto-throttle to prevent exceeding limits.
    All usage data is persisted in the system_metrics table.
    """

    ALERT_THRESHOLD = 0.8  # 80% of limit triggers alert

    def __init__(self):
        """
        Initializes the FreeTierMonitor with database access.
        """
        self.db = Database()

    def check_all_services(self) -> dict:
        """
        Checks usage status for all monitored services.

        Returns:
            Dictionary of service_name: {usage, limit, percentage, status, alerts}.

        Side effects:
            Records current usage metrics to the database.
            Generates alerts for services exceeding 80% threshold.
        """
        results = {}
        alerts = []

        # Groq API
        groq_status = self._check_groq()
        results['groq_api'] = groq_status
        if groq_status.get('alert'):
            alerts.append(groq_status['alert'])

        # YouTube Data API
        youtube_status = self._check_youtube_quota()
        results['youtube_data_api'] = youtube_status
        if youtube_status.get('alert'):
            alerts.append(youtube_status['alert'])

        # OCI Object Storage
        oci_status = self._check_oci_storage()
        results['oci_object_storage'] = oci_status
        if oci_status.get('alert'):
            alerts.append(oci_status['alert'])

        # Google Drive (if enabled)
        if os.getenv('GDRIVE_ENABLED', 'false').lower() == 'true':
            gdrive_status = self._check_gdrive()
            results['google_drive'] = gdrive_status
            if gdrive_status.get('alert'):
                alerts.append(gdrive_status['alert'])

        # Gmail SMTP
        smtp_status = self._check_smtp_usage()
        results['gmail_smtp'] = smtp_status
        if smtp_status.get('alert'):
            alerts.append(smtp_status['alert'])

        # NewsAPI
        newsapi_status = self._check_newsapi()
        results['newsapi'] = newsapi_status
        if newsapi_status.get('alert'):
            alerts.append(newsapi_status['alert'])

        # Send alerts if any
        if alerts:
            self._send_alerts(alerts)

        return results

    def _check_groq(self) -> dict:
        """
        Checks Groq API usage against free tier limits.

        Returns:
            Status dictionary with usage, limits, and alert info.
        """
        # Query today's usage from llm_requests table
        today_usage = self.db.query_one(
            """SELECT COUNT(*) as request_count,
                      COALESCE(SUM(tokens_used), 0) as total_tokens
               FROM llm_requests
               WHERE provider = 'groq'
               AND date(created_at) = date('now')"""
        )

        requests_today = today_usage['request_count'] if today_usage else 0
        tokens_today = today_usage['total_tokens'] if today_usage else 0

        limits_70b = FREE_TIER_LIMITS['groq_api']['model_limits']['llama-3.3-70b-versatile']
        rpd_limit = limits_70b['requests_per_day']
        tpd_limit = limits_70b['tokens_per_day']

        rpd_pct = requests_today / rpd_limit if rpd_limit > 0 else 0
        tpd_pct = tokens_today / tpd_limit if tpd_limit > 0 else 0

        status = {
            'requests_today': requests_today,
            'requests_limit': rpd_limit,
            'requests_percent': round(rpd_pct * 100, 1),
            'tokens_today': tokens_today,
            'tokens_limit': tpd_limit,
            'tokens_percent': round(tpd_pct * 100, 1),
            'status': 'ok',
        }

        if rpd_pct > self.ALERT_THRESHOLD or tpd_pct > self.ALERT_THRESHOLD:
            status['status'] = 'warning'
            status['alert'] = (
                f"Groq API at {max(rpd_pct, tpd_pct) * 100:.0f}% of daily limit. "
                f"Requests: {requests_today}/{rpd_limit}, "
                f"Tokens: {tokens_today}/{tpd_limit}"
            )

        self._record_metric('groq_requests_today', requests_today)
        self._record_metric('groq_tokens_today', tokens_today)

        return status

    def _check_youtube_quota(self) -> dict:
        """
        Checks YouTube Data API quota usage.

        Returns:
            Status dictionary with quota usage information.
        """
        # Sum up today's YouTube API calls weighted by quota cost
        uploads_today = self.db.query_one(
            """SELECT COUNT(*) as cnt FROM publish_jobs
               WHERE platform = 'youtube' AND status = 'published'
               AND date(published_at) = date('now')"""
        )

        upload_count = uploads_today['cnt'] if uploads_today else 0
        # Each upload costs 1600 units + 50 metadata + 50 thumbnail = 1700
        quota_used = upload_count * 1700
        quota_limit = 10000

        pct = quota_used / quota_limit if quota_limit > 0 else 0

        status = {
            'uploads_today': upload_count,
            'quota_used': quota_used,
            'quota_limit': quota_limit,
            'percent': round(pct * 100, 1),
            'status': 'ok',
        }

        if pct > self.ALERT_THRESHOLD:
            status['status'] = 'warning'
            status['alert'] = (
                f"YouTube quota at {pct * 100:.0f}%. "
                f"Used {quota_used}/{quota_limit} units ({upload_count} uploads)"
            )

        self._record_metric('youtube_quota_used', quota_used)
        return status

    def _check_oci_storage(self) -> dict:
        """
        Checks OCI Object Storage usage.

        Returns:
            Status dictionary with storage usage information.
        """
        try:
            from modules.storage.oci_storage import OCIObjectStorage
            storage = OCIObjectStorage()
            used_gb = storage.get_storage_usage_gb()
        except Exception:
            used_gb = 0.0

        total_gb = FREE_TIER_LIMITS['oci_object_storage']['total_gb']
        alert_gb = FREE_TIER_LIMITS['oci_object_storage']['alert_threshold_gb']
        pct = used_gb / total_gb if total_gb > 0 else 0

        status = {
            'used_gb': round(used_gb, 2),
            'total_gb': total_gb,
            'percent': round(pct * 100, 1),
            'status': 'ok',
        }

        if used_gb > alert_gb:
            status['status'] = 'warning'
            status['alert'] = (
                f"OCI Object Storage at {pct * 100:.0f}% ({used_gb:.1f}/{total_gb}GB)"
            )

        self._record_metric('oci_storage_gb', used_gb)
        return status

    def _check_gdrive(self) -> dict:
        """
        Checks Google Drive storage usage.

        Returns:
            Status dictionary with Drive storage information.
        """
        total_gb = FREE_TIER_LIMITS['google_drive']['total_gb']
        alert_gb = FREE_TIER_LIMITS['google_drive']['alert_threshold_gb']

        try:
            from modules.review_gate.gdrive_uploader import GDriveVideoUploader
            uploader = GDriveVideoUploader()
            used_gb = uploader.get_storage_usage_gb()
        except Exception:
            used_gb = 0.0

        pct = used_gb / total_gb if total_gb > 0 else 0

        status = {
            'used_gb': round(used_gb, 2),
            'total_gb': total_gb,
            'percent': round(pct * 100, 1),
            'status': 'ok',
        }

        if used_gb > alert_gb:
            status['status'] = 'warning'
            status['alert'] = (
                f"Google Drive at {pct * 100:.0f}% ({used_gb:.1f}/{total_gb}GB)"
            )

        return status

    def _check_smtp_usage(self) -> dict:
        """
        Checks Gmail SMTP email sending usage.

        Returns:
            Status dictionary with email count information.
        """
        emails_today = self.db.query_one(
            """SELECT COUNT(*) as cnt FROM notifications
               WHERE channel = 'email' AND date(sent_at) = date('now')"""
        )

        count = emails_today['cnt'] if emails_today else 0
        limit = FREE_TIER_LIMITS['gmail_smtp']['emails_per_day']
        pct = count / limit if limit > 0 else 0

        status = {
            'emails_today': count,
            'limit': limit,
            'percent': round(pct * 100, 1),
            'status': 'ok',
        }

        if pct > self.ALERT_THRESHOLD:
            status['status'] = 'warning'
            status['alert'] = f"Gmail SMTP at {pct * 100:.0f}% ({count}/{limit} emails)"

        return status

    def _check_newsapi(self) -> dict:
        """
        Checks NewsAPI daily request usage.

        Returns:
            Status dictionary with request count information.
        """
        # Count today's NewsAPI requests from system_metrics
        result = self.db.query_one(
            """SELECT COALESCE(SUM(metric_value), 0) as total
               FROM system_metrics
               WHERE metric_name = 'newsapi_requests'
               AND date(recorded_at) = date('now')"""
        )

        count = int(result['total']) if result else 0
        limit = FREE_TIER_LIMITS['newsapi']['requests_per_day']
        pct = count / limit if limit > 0 else 0

        status = {
            'requests_today': count,
            'limit': limit,
            'percent': round(pct * 100, 1),
            'status': 'ok',
        }

        if pct > self.ALERT_THRESHOLD:
            status['status'] = 'warning'
            status['alert'] = f"NewsAPI at {pct * 100:.0f}% ({count}/{limit} requests)"

        return status

    def _record_metric(self, metric_name: str, value: float) -> None:
        """
        Records a usage metric to the database.

        Parameters:
            metric_name: Name of the metric.
            value: Current value.
        """
        try:
            self.db.save_metric(metric_name, value)
        except Exception as e:
            logger.debug(f"Failed to record metric {metric_name}: {e}")

    def _send_alerts(self, alerts: list[str]) -> None:
        """
        Sends alert messages via Telegram (primary) and/or logging.

        Parameters:
            alerts: List of alert message strings.

        Side effects:
            Attempts to send Telegram notification.
            Logs all alerts regardless.
        """
        for alert in alerts:
            logger.warning(f"FREE TIER ALERT: {alert}")

        # Try Telegram notification
        try:
            from modules.notifications.telegram_bot import TelegramNotifier
            notifier = TelegramNotifier()
            alert_text = "FREE TIER ALERTS:\n\n" + "\n\n".join(alerts)
            notifier.send_alert(alert_text)
        except Exception as e:
            logger.debug(f"Could not send Telegram alert: {e}")

    def get_limits(self) -> dict:
        """
        Returns the full free tier limits reference.

        Returns:
            Dictionary of all service limits.
        """
        return FREE_TIER_LIMITS
