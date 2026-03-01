"""
Health monitoring module for AutoFarm Zero — Success Guru Network v6.0.

Provides comprehensive system health checking for the /health endpoint
and daily digest reports. Monitors system resources, service availability,
API quotas, content pipeline status, and storage usage.

Expanded from V5.1 with new checks for:
- Ollama health and response latency
- Groq rate limit remaining
- Telegram bot connectivity
- Orphaned job count
- Swap usage (should be near zero normally)
- OCI idle guard status
"""

import os
import time
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import psutil
import requests
import structlog

logger = structlog.get_logger(__name__)


class HealthMonitor:
    """
    Comprehensive system health monitor for AutoFarm Zero.

    Performs health checks on all system components:
    - System resources (CPU, RAM, swap, disk)
    - Services (Ollama, SQLite, Squid proxies, Telegram bot)
    - API quotas (Groq, YouTube, Pexels)
    - Content pipeline (queue depth, orphaned jobs, review queue)
    - Storage (OCI Object Storage, Google Drive)

    Results are returned as a structured dict suitable for the
    /health endpoint and daily digest notifications.

    Attributes:
        OLLAMA_TIMEOUT: Timeout for Ollama health check in seconds.
        DISK_CRITICAL_PERCENT: Disk usage percentage that triggers critical alert.
        SWAP_WARNING_GB: Swap usage in GB that triggers warning.
    """

    OLLAMA_TIMEOUT: int = 10
    DISK_CRITICAL_PERCENT: float = 90.0
    SWAP_WARNING_GB: float = 2.0
    RAM_CRITICAL_PERCENT: float = 90.0

    def __init__(self) -> None:
        """
        Initializes the HealthMonitor with database access.

        Side effects:
            Creates a Database instance for querying pipeline status.
        """
        from database.db import Database
        self.db = Database()

    def full_health_check(self) -> dict:
        """
        Returns comprehensive system health for /health endpoint and daily digest.

        Returns:
            Dict with sections: system, services, api_quotas,
            content_pipeline, storage, overall_status, timestamp.
            Each section contains detailed metrics and status indicators.

        Side effects:
            Performs HTTP requests to check Ollama, Telegram, proxies.
            Queries the database for pipeline metrics.
            Records metrics to system_metrics table.
        """
        start_time = time.time()

        health = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'system': self._check_system_resources(),
            'services': {
                'ollama': self._check_ollama(),
                'sqlite': self._check_sqlite(),
                'squid_proxies': self._check_all_proxies(),
                'telegram_bot': self._check_telegram(),
            },
            'api_quotas': {
                'groq': self._check_groq_quota(),
                'youtube': self._check_youtube_quota(),
                'pexels': self._check_pexels_quota(),
            },
            'content_pipeline': {
                'queue_depth': self._check_queue_depth(),
                'orphaned_jobs': self._count_orphaned_jobs(),
                'last_publish_per_brand': self._last_publish_times(),
                'review_queue_age': self._oldest_pending_review(),
            },
            'storage': {
                'oci_object_storage': self._check_oci_storage(),
                'google_drive': self._check_gdrive_storage(),
            },
        }

        # Calculate overall status
        health['overall_status'] = self._calculate_overall_status(health)
        health['check_duration_ms'] = int((time.time() - start_time) * 1000)

        # Record check in database
        self._record_health_check(health)

        return health

    def quick_health_check(self) -> dict:
        """
        Returns a fast, lightweight health check for frequent polling.

        Returns:
            Dict with system resources and critical service status only.

        Side effects:
            Minimal — only checks psutil metrics and SQLite connectivity.
        """
        return {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'system': self._check_system_resources(),
            'sqlite': self._check_sqlite(),
            'status': 'ok' if self._check_sqlite().get('status') == 'ok' else 'error',
        }

    def _check_system_resources(self) -> dict:
        """
        Checks CPU, RAM, swap, and disk usage.

        Returns:
            Dict with cpu_percent, ram_used_gb, ram_total_gb, ram_percent,
            swap_used_gb, swap_total_gb, disk_used_percent, disk_free_gb.

        Side effects:
            None (reads psutil metrics only).
        """
        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()

        # Try /app first, fall back to root
        try:
            disk = psutil.disk_usage('/app')
        except FileNotFoundError:
            disk = psutil.disk_usage('/')

        cpu_percent = psutil.cpu_percent(interval=1)

        status = 'ok'
        warnings = []

        if mem.percent > self.RAM_CRITICAL_PERCENT:
            status = 'critical'
            warnings.append(f"RAM usage at {mem.percent}%")
        elif mem.percent > 80:
            status = 'warning'
            warnings.append(f"RAM usage at {mem.percent}%")

        if swap.used / (1024 ** 3) > self.SWAP_WARNING_GB:
            if status != 'critical':
                status = 'warning'
            warnings.append(f"Swap usage: {swap.used / (1024**3):.1f}GB")

        if disk.percent > self.DISK_CRITICAL_PERCENT:
            status = 'critical'
            warnings.append(f"Disk usage at {disk.percent}%")
        elif disk.percent > 80:
            if status != 'critical':
                status = 'warning'
            warnings.append(f"Disk usage at {disk.percent}%")

        return {
            'cpu_percent': cpu_percent,
            'ram_used_gb': round(mem.used / (1024 ** 3), 2),
            'ram_total_gb': round(mem.total / (1024 ** 3), 2),
            'ram_percent': mem.percent,
            'swap_used_gb': round(swap.used / (1024 ** 3), 2),
            'swap_total_gb': round(swap.total / (1024 ** 3), 2),
            'disk_used_percent': disk.percent,
            'disk_free_gb': round(disk.free / (1024 ** 3), 2),
            'status': status,
            'warnings': warnings,
        }

    def _check_ollama(self) -> dict:
        """
        Checks if Ollama is responsive and measures latency.

        Returns:
            Dict with status ('ok', 'slow', 'error'), latency_ms,
            model_loaded (bool), and any error message.

        Side effects:
            Makes HTTP request to local Ollama instance.
        """
        ollama_host = os.getenv('OLLAMA_HOST', 'http://localhost:11434')
        if not ollama_host.startswith('http'):
            ollama_host = f'http://{ollama_host}'

        try:
            start = time.time()
            response = requests.get(
                f'{ollama_host}/api/tags',
                timeout=self.OLLAMA_TIMEOUT
            )
            latency_ms = int((time.time() - start) * 1000)

            if response.status_code == 200:
                data = response.json()
                models = data.get('models', [])
                model_loaded = any(
                    'llama' in m.get('name', '').lower()
                    for m in models
                )

                status = 'ok'
                if latency_ms > 5000:
                    status = 'slow'

                return {
                    'status': status,
                    'latency_ms': latency_ms,
                    'model_loaded': model_loaded,
                    'models': [m.get('name') for m in models],
                }
            else:
                return {
                    'status': 'error',
                    'latency_ms': latency_ms,
                    'error': f'HTTP {response.status_code}',
                }

        except requests.Timeout:
            return {
                'status': 'error',
                'error': f'Timeout after {self.OLLAMA_TIMEOUT}s',
            }
        except requests.ConnectionError:
            return {
                'status': 'error',
                'error': 'Connection refused — Ollama not running',
            }
        except Exception as e:
            return {
                'status': 'error',
                'error': str(e),
            }

    def _check_sqlite(self) -> dict:
        """
        Checks SQLite database health with integrity check.

        Returns:
            Dict with status ('ok' or 'error'), db_size_mb,
            wal_size_mb, and table_count.

        Side effects:
            Executes PRAGMA integrity_check (lightweight).
            Reads file sizes from disk.
        """
        try:
            # Quick integrity check
            result = self.db.fetch_one("PRAGMA quick_check")
            integrity_ok = result and (
                result[0] == 'ok' if isinstance(result, (list, tuple))
                else result.get('quick_check', '') == 'ok'
            )

            # DB file size
            db_path = os.getenv('DATABASE_PATH', '/app/data/autofarm.db')
            db_size_mb = 0.0
            wal_size_mb = 0.0
            if os.path.exists(db_path):
                db_size_mb = os.path.getsize(db_path) / (1024 * 1024)
            wal_path = db_path + '-wal'
            if os.path.exists(wal_path):
                wal_size_mb = os.path.getsize(wal_path) / (1024 * 1024)

            # Table count
            tables = self.db.fetch_all(
                "SELECT COUNT(*) as cnt FROM sqlite_master "
                "WHERE type='table'"
            )
            table_count = tables[0]['cnt'] if tables else 0

            return {
                'status': 'ok' if integrity_ok else 'error',
                'integrity': 'ok' if integrity_ok else 'failed',
                'db_size_mb': round(db_size_mb, 2),
                'wal_size_mb': round(wal_size_mb, 2),
                'table_count': table_count,
            }

        except Exception as e:
            return {
                'status': 'error',
                'error': str(e),
            }

    def _check_all_proxies(self) -> dict:
        """
        Checks connectivity to all 6 Squid proxy instances.

        Returns:
            Dict with overall status, and per-brand proxy status.
            Each brand entry has status, latency_ms, ip_address.

        Side effects:
            Makes HTTP CONNECT requests through each proxy.
        """
        from config.settings import PROXY_PORTS, PROXY_VM_INTERNAL_IP

        proxy_host = PROXY_VM_INTERNAL_IP or os.getenv(
            'PROXY_VM_INTERNAL_IP', '10.0.2.2'
        )

        results = {}
        all_ok = True

        for brand_id, port in PROXY_PORTS.items():
            proxy_url = f'http://{proxy_host}:{port}'
            try:
                start = time.time()
                response = requests.get(
                    'http://httpbin.org/ip',
                    proxies={'http': proxy_url, 'https': proxy_url},
                    timeout=10
                )
                latency_ms = int((time.time() - start) * 1000)

                if response.status_code == 200:
                    ip_data = response.json()
                    results[brand_id] = {
                        'status': 'ok',
                        'latency_ms': latency_ms,
                        'ip_address': ip_data.get('origin', 'unknown'),
                    }
                else:
                    results[brand_id] = {
                        'status': 'error',
                        'error': f'HTTP {response.status_code}',
                    }
                    all_ok = False

            except Exception as e:
                results[brand_id] = {
                    'status': 'error',
                    'error': str(e),
                }
                all_ok = False

        return {
            'overall_status': 'ok' if all_ok else 'degraded',
            'proxies': results,
        }

    def _check_telegram(self) -> dict:
        """
        Checks Telegram bot connectivity.

        Returns:
            Dict with status, bot_username, and any error.

        Side effects:
            Makes HTTP request to Telegram Bot API.
        """
        bot_token = os.getenv('TELEGRAM_BOT_TOKEN', '')
        if not bot_token:
            return {'status': 'not_configured'}

        try:
            response = requests.get(
                f'https://api.telegram.org/bot{bot_token}/getMe',
                timeout=10
            )

            if response.status_code == 200:
                data = response.json()
                if data.get('ok'):
                    return {
                        'status': 'ok',
                        'bot_username': data['result'].get('username', ''),
                    }

            return {
                'status': 'error',
                'error': f'HTTP {response.status_code}',
            }

        except Exception as e:
            return {
                'status': 'error',
                'error': str(e),
            }

    def _check_groq_quota(self) -> dict:
        """
        Checks remaining Groq API quota for the day.

        Returns:
            Dict with status, requests_used, tokens_used,
            requests_limit, tokens_limit, percent_used.

        Side effects:
            Queries llm_requests table for today's usage.
        """
        try:
            today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
            row = self.db.fetch_one(
                "SELECT COUNT(*) as req_count, "
                "COALESCE(SUM(tokens_used), 0) as tokens_total "
                "FROM llm_requests "
                "WHERE provider='groq' AND DATE(created_at)=?",
                (today,)
            )

            requests_used = row['req_count'] if row else 0
            tokens_used = row['tokens_total'] if row else 0

            # Groq free tier limits
            req_limit = 1000
            token_limit = 100000

            percent_req = (requests_used / req_limit) * 100 if req_limit else 0
            percent_tok = (tokens_used / token_limit) * 100 if token_limit else 0

            status = 'ok'
            if percent_req > 80 or percent_tok > 80:
                status = 'warning'
            if percent_req > 95 or percent_tok > 95:
                status = 'critical'

            return {
                'status': status,
                'requests_used': requests_used,
                'requests_limit': req_limit,
                'tokens_used': tokens_used,
                'tokens_limit': token_limit,
                'percent_requests_used': round(percent_req, 1),
                'percent_tokens_used': round(percent_tok, 1),
            }

        except Exception as e:
            return {'status': 'error', 'error': str(e)}

    def _check_youtube_quota(self) -> dict:
        """
        Checks remaining YouTube API quota for the day.

        Returns:
            Dict with status, units_used, units_limit, percent_used.

        Side effects:
            Queries publish_jobs table for today's YouTube uploads.
        """
        try:
            today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
            row = self.db.fetch_one(
                "SELECT COUNT(*) as upload_count "
                "FROM publish_jobs "
                "WHERE platform='youtube' AND DATE(published_at)=?",
                (today,)
            )

            uploads = row['upload_count'] if row else 0
            # Each upload costs 1600 quota units
            units_used = uploads * 1600
            units_limit = 10000
            percent_used = (units_used / units_limit) * 100 if units_limit else 0

            status = 'ok'
            if percent_used > 80:
                status = 'warning'
            if percent_used > 95:
                status = 'critical'

            return {
                'status': status,
                'uploads_today': uploads,
                'units_used': units_used,
                'units_limit': units_limit,
                'percent_used': round(percent_used, 1),
            }

        except Exception as e:
            return {'status': 'error', 'error': str(e)}

    def _check_pexels_quota(self) -> dict:
        """
        Checks Pexels API usage (200 requests/hour, 20,000/month).

        Returns:
            Dict with status and usage metrics.

        Side effects:
            Queries system_metrics for recent Pexels usage.
        """
        try:
            # Check last hour's requests
            one_hour_ago = (
                datetime.now(timezone.utc) - timedelta(hours=1)
            ).isoformat()

            row = self.db.fetch_one(
                "SELECT COUNT(*) as req_count "
                "FROM system_metrics "
                "WHERE metric_name='pexels_api_call' AND recorded_at > ?",
                (one_hour_ago,)
            )

            requests_this_hour = row['req_count'] if row else 0
            hourly_limit = 200
            percent_used = (requests_this_hour / hourly_limit) * 100

            status = 'ok'
            if percent_used > 80:
                status = 'warning'

            return {
                'status': status,
                'requests_this_hour': requests_this_hour,
                'hourly_limit': hourly_limit,
                'percent_used': round(percent_used, 1),
            }

        except Exception as e:
            return {'status': 'error', 'error': str(e)}

    def _check_queue_depth(self) -> dict:
        """
        Checks content queue depth per brand.

        Returns:
            Dict with total queue depth and per-brand breakdown.

        Side effects:
            Queries content_queue table.
        """
        try:
            rows = self.db.fetch_all(
                "SELECT brand_id, COUNT(*) as count "
                "FROM content_queue WHERE status='ready' "
                "GROUP BY brand_id"
            )

            per_brand = {row['brand_id']: row['count'] for row in rows}
            total = sum(per_brand.values())

            status = 'ok'
            if total == 0:
                status = 'warning'  # Queue empty — content needed

            return {
                'status': status,
                'total': total,
                'per_brand': per_brand,
            }

        except Exception as e:
            return {'status': 'error', 'error': str(e)}

    def _count_orphaned_jobs(self) -> dict:
        """
        Counts jobs stuck in non-terminal states for too long.

        Returns:
            Dict with count of orphaned jobs and their states.

        Side effects:
            Queries job_states table for stale entries.
        """
        try:
            # Jobs stuck for more than 6 hours
            cutoff = (
                datetime.now(timezone.utc) - timedelta(hours=6)
            ).isoformat()

            rows = self.db.fetch_all(
                "SELECT state, COUNT(*) as count "
                "FROM job_states "
                "WHERE state NOT IN ('PUBLISHED', 'FAILED', 'REJECTED') "
                "AND updated_at < ? "
                "GROUP BY state",
                (cutoff,)
            )

            orphans = {row['state']: row['count'] for row in rows}
            total = sum(orphans.values())

            return {
                'status': 'ok' if total == 0 else 'warning',
                'total_orphaned': total,
                'by_state': orphans,
            }

        except Exception as e:
            return {'status': 'error', 'error': str(e)}

    def _last_publish_times(self) -> dict:
        """
        Returns the time of the most recent publish per brand.

        Returns:
            Dict with brand_id → last_published ISO timestamp.

        Side effects:
            Queries publish_jobs table.
        """
        try:
            rows = self.db.fetch_all(
                "SELECT brand_id, MAX(published_at) as last_publish "
                "FROM publish_jobs WHERE status='published' "
                "GROUP BY brand_id"
            )

            return {row['brand_id']: row['last_publish'] for row in rows}

        except Exception as e:
            return {'error': str(e)}

    def _oldest_pending_review(self) -> dict:
        """
        Returns the age of the oldest pending review.

        Returns:
            Dict with oldest_review_age_hours, total_pending,
            and status (warning if >24h old).

        Side effects:
            Queries reviews table.
        """
        try:
            row = self.db.fetch_one(
                "SELECT MIN(created_at) as oldest, COUNT(*) as pending "
                "FROM reviews WHERE status='pending'"
            )

            if not row or not row['oldest']:
                return {
                    'status': 'ok',
                    'total_pending': 0,
                    'oldest_review_age_hours': 0,
                }

            oldest = datetime.fromisoformat(row['oldest'])
            age_hours = (datetime.now(timezone.utc) - oldest.replace(
                tzinfo=timezone.utc)).total_seconds() / 3600

            status = 'ok'
            if age_hours > 48:
                status = 'critical'
            elif age_hours > 24:
                status = 'warning'

            return {
                'status': status,
                'total_pending': row['pending'],
                'oldest_review_age_hours': round(age_hours, 1),
            }

        except Exception as e:
            return {'status': 'error', 'error': str(e)}

    def _check_oci_storage(self) -> dict:
        """
        Checks OCI Object Storage usage.

        Returns:
            Dict with status, used_gb, limit_gb, percent_used.

        Side effects:
            Instantiates OCIObjectStorage and queries bucket.
        """
        try:
            from modules.storage.oci_storage import OCIObjectStorage
            storage = OCIObjectStorage()
            usage_gb = storage.get_storage_usage_gb()

            status = 'ok'
            if usage_gb > 16:
                status = 'critical'
            elif usage_gb > 12:
                status = 'warning'

            return {
                'status': status,
                'used_gb': usage_gb,
                'limit_gb': 20,
                'percent_used': round((usage_gb / 20) * 100, 1),
            }

        except Exception as e:
            return {'status': 'unknown', 'error': str(e)}

    def _check_gdrive_storage(self) -> dict:
        """
        Checks Google Drive storage usage.
        Alerts at >12GB (80% of 15GB). Forces cleanup at critical threshold.

        Returns:
            Dict with status, used_gb, limit_gb.

        Side effects:
            May trigger cleanup if usage is critical.
        """
        try:
            from modules.review_gate.gdrive_uploader import GDriveVideoUploader
            uploader = GDriveVideoUploader()
            used_gb = uploader.get_storage_usage_gb()

            status = 'ok'
            if used_gb > 12:
                status = 'critical'
                uploader.cleanup_expired_reviews()
            elif used_gb > 9:
                status = 'warning'

            return {
                'service': 'google_drive',
                'used_gb': round(used_gb, 2),
                'limit_gb': 15,
                'status': status,
            }

        except ImportError:
            return {'status': 'not_configured', 'note': 'GDrive is optional'}
        except Exception as e:
            return {'status': 'unknown', 'error': str(e)}

    def _calculate_overall_status(self, health: dict) -> str:
        """
        Calculates the overall system health status.

        Parameters:
            health: Full health check result dict.

        Returns:
            One of: 'healthy', 'degraded', 'critical'.
        """
        critical_services = ['sqlite']
        statuses = []

        # Check system
        statuses.append(health.get('system', {}).get('status', 'ok'))

        # Check all services
        for svc_name, svc_data in health.get('services', {}).items():
            svc_status = svc_data.get('status', 'ok') if isinstance(
                svc_data, dict) else 'ok'
            if svc_name in critical_services and svc_status == 'error':
                return 'critical'
            statuses.append(svc_status)

        # Check API quotas
        for quota_data in health.get('api_quotas', {}).values():
            if isinstance(quota_data, dict):
                statuses.append(quota_data.get('status', 'ok'))

        # Check pipeline
        pipeline = health.get('content_pipeline', {})
        orphaned = pipeline.get('orphaned_jobs', {})
        if isinstance(orphaned, dict) and orphaned.get('total_orphaned', 0) > 10:
            return 'critical'

        # Determine overall
        if 'critical' in statuses or 'error' in statuses:
            return 'critical'
        if 'warning' in statuses or 'degraded' in statuses:
            return 'degraded'
        return 'healthy'

    def _record_health_check(self, health: dict) -> None:
        """
        Records key health metrics to system_metrics table.

        Parameters:
            health: Full health check result dict.

        Side effects:
            Inserts metrics into system_metrics table.
        """
        try:
            system = health.get('system', {})
            metrics = [
                ('cpu_percent', system.get('cpu_percent', 0), None),
                ('ram_percent', system.get('ram_percent', 0), None),
                ('disk_percent', system.get('disk_used_percent', 0), None),
                ('swap_used_gb', system.get('swap_used_gb', 0), None),
            ]

            for name, value, label in metrics:
                self.db.execute_write(
                    "INSERT INTO system_metrics "
                    "(metric_name, metric_value, label) VALUES (?, ?, ?)",
                    (name, value, label)
                )

        except Exception as e:
            logger.warning("record_health_metrics_failed", error=str(e))
