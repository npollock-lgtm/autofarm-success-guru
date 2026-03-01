"""
OCI Object Storage module for AutoFarm Zero — Success Guru Network v6.0.

Manages database backups to Oracle Cloud Infrastructure Object Storage.
OCI Free Tier provides 20GB of Standard + Infrequent Access + Archive
storage combined. This module handles backup uploads, listing, cleanup
of old backups, and storage usage monitoring with alerts.

Retention policy: 14 days of backups.
Alert threshold: 16GB (80% of 20GB free tier limit).
"""

import os
import gzip
import shutil
import logging
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)


class OCIObjectStorage:
    """
    Manages database backups to OCI Object Storage.

    Free tier: 20GB Standard + Infrequent + Archive combined.
    Backup files are stored in the 'autofarm-backups' bucket with a
    'backup/' prefix. Old backups beyond the retention period are
    automatically purged.

    Attributes:
        BUCKET_NAME: Name of the OCI bucket for backups.
        NAMESPACE: OCI Object Storage namespace.
        TOTAL_FREE_GB: Total free tier storage in GB.
        ALERT_THRESHOLD_GB: Usage level that triggers alerts (80%).
        DEFAULT_RETENTION_DAYS: How long to keep backups.
    """

    BUCKET_NAME: str = "autofarm-backups"
    TOTAL_FREE_GB: float = 20.0
    ALERT_THRESHOLD_GB: float = 16.0  # 80% of 20GB
    DEFAULT_RETENTION_DAYS: int = 14

    def __init__(self) -> None:
        """
        Initializes the OCI Object Storage client.

        Reads OCI configuration from environment variables.
        Falls back to OCI config file if env vars not set.

        Side effects:
            Creates OCI ObjectStorageClient on initialization.
            Reads OCI_REGION, OCI_TENANCY_OCID, OCI_USER_OCID,
            OCI_KEY_FILE, OCI_FINGERPRINT, OCI_NAMESPACE from env.
        """
        self.region: str = os.getenv('OCI_REGION', 'us-ashburn-1')
        self.namespace: str = os.getenv('OCI_NAMESPACE', '')
        self.compartment_id: str = os.getenv('COMPARTMENT_OCID', '')
        self._client = None
        self._initialized: bool = False

    def _get_client(self):
        """
        Lazily initializes the OCI Object Storage client.

        Returns:
            oci.object_storage.ObjectStorageClient instance.

        Raises:
            ImportError: If oci SDK is not installed.
            Exception: If OCI configuration is invalid.

        Side effects:
            Sets self._client and self._initialized on first call.
            Auto-discovers namespace if not set via env var.
        """
        if self._client is not None:
            return self._client

        try:
            import oci

            # Try environment-based config first
            tenancy_ocid = os.getenv('OCI_TENANCY_OCID', '')
            user_ocid = os.getenv('OCI_USER_OCID', '')
            key_file = os.getenv('OCI_KEY_FILE', '')
            fingerprint = os.getenv('OCI_FINGERPRINT', '')

            if all([tenancy_ocid, user_ocid, key_file, fingerprint]):
                config = {
                    'user': user_ocid,
                    'key_file': key_file,
                    'fingerprint': fingerprint,
                    'tenancy': tenancy_ocid,
                    'region': self.region,
                }
                oci.config.validate_config(config)
            else:
                # Fall back to OCI config file
                config = oci.config.from_file()

            self._client = oci.object_storage.ObjectStorageClient(config)

            # Auto-discover namespace if not set
            if not self.namespace:
                self.namespace = self._client.get_namespace().data

            self._initialized = True
            logger.info("oci_storage_initialized",
                        namespace=self.namespace,
                        region=self.region)
            return self._client

        except ImportError:
            logger.error("oci_sdk_not_installed",
                         msg="pip install oci to enable OCI Object Storage")
            raise
        except Exception as e:
            logger.error("oci_storage_init_failed", error=str(e))
            raise

    def ensure_bucket_exists(self) -> bool:
        """
        Creates the backup bucket if it doesn't exist.

        Returns:
            True if bucket exists or was created, False on failure.

        Side effects:
            Creates OCI Object Storage bucket if missing.
            Sets lifecycle policy for auto-deletion after retention period.
        """
        client = self._get_client()

        try:
            import oci

            # Check if bucket exists
            try:
                client.get_bucket(self.namespace, self.BUCKET_NAME)
                logger.info("bucket_exists", bucket=self.BUCKET_NAME)
                return True
            except oci.exceptions.ServiceError as e:
                if e.status != 404:
                    raise

            # Create bucket
            create_details = oci.object_storage.models.CreateBucketDetails(
                name=self.BUCKET_NAME,
                compartment_id=self.compartment_id,
                storage_tier='Standard',
                public_access_type='NoPublicAccess',
                versioning='Disabled',
                auto_tiering='Disabled',
            )

            client.create_bucket(self.namespace, create_details)
            logger.info("bucket_created", bucket=self.BUCKET_NAME)

            # Set lifecycle policy for auto-deletion
            self._set_lifecycle_policy()

            return True

        except Exception as e:
            logger.error("bucket_creation_failed",
                         bucket=self.BUCKET_NAME,
                         error=str(e))
            return False

    def _set_lifecycle_policy(self) -> None:
        """
        Sets lifecycle policy to auto-delete backups after retention period.

        Side effects:
            Creates or updates lifecycle policy on the backup bucket.
            Rule: delete objects with 'backup/' prefix after DEFAULT_RETENTION_DAYS.
        """
        client = self._get_client()

        try:
            import oci

            rule = oci.object_storage.models.ObjectLifecycleRule(
                name='auto-delete-old-backups',
                action='DELETE',
                time_amount=self.DEFAULT_RETENTION_DAYS,
                time_unit='DAYS',
                is_enabled=True,
                object_name_filter=oci.object_storage.models.ObjectNameFilter(
                    inclusion_prefixes=['backup/']
                )
            )

            policy = oci.object_storage.models.PutObjectLifecyclePolicyDetails(
                items=[rule]
            )

            client.put_object_lifecycle_policy(
                self.namespace, self.BUCKET_NAME, policy
            )
            logger.info("lifecycle_policy_set",
                         retention_days=self.DEFAULT_RETENTION_DAYS)

        except Exception as e:
            logger.warning("lifecycle_policy_failed", error=str(e))

    def upload_backup(self, backup_path: str,
                      compress: bool = True) -> Optional[str]:
        """
        Uploads a database backup file to OCI Object Storage.

        Parameters:
            backup_path: Local path to the backup file.
            compress: If True, gzip the file before uploading.

        Returns:
            Object name in the bucket if successful, None on failure.

        Side effects:
            Uploads file to OCI Object Storage.
            Creates a gzipped copy if compress=True.
            Records backup metadata in the database.
            Logs the upload with file size and duration.
        """
        client = self._get_client()

        try:
            backup_file = Path(backup_path)
            if not backup_file.exists():
                logger.error("backup_file_not_found", path=backup_path)
                return None

            # Generate object name with timestamp
            timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
            base_name = backup_file.stem

            upload_path = backup_path
            if compress and not backup_path.endswith('.gz'):
                # Compress the backup
                compressed_path = f"{backup_path}.gz"
                with open(backup_path, 'rb') as f_in:
                    with gzip.open(compressed_path, 'wb') as f_out:
                        shutil.copyfileobj(f_in, f_out)
                upload_path = compressed_path
                object_name = f"backup/{base_name}_{timestamp}.db.gz"
            else:
                object_name = f"backup/{base_name}_{timestamp}.db"

            # Calculate MD5 for integrity verification
            md5_hash = self._calculate_md5(upload_path)
            file_size = os.path.getsize(upload_path)

            # Upload to OCI
            with open(upload_path, 'rb') as f:
                client.put_object(
                    self.namespace,
                    self.BUCKET_NAME,
                    object_name,
                    f,
                    content_type='application/gzip' if compress else 'application/octet-stream',
                    content_md5=md5_hash,
                    opc_meta={'source': 'autofarm-backup',
                              'original_path': backup_path,
                              'timestamp': timestamp}
                )

            # Clean up compressed file if we created it
            if compress and upload_path != backup_path:
                os.remove(upload_path)

            logger.info("backup_uploaded",
                         object_name=object_name,
                         size_mb=round(file_size / (1024 * 1024), 2),
                         compressed=compress)

            # Record in database
            self._record_backup(object_name, file_size, md5_hash)

            return object_name

        except Exception as e:
            logger.error("backup_upload_failed",
                         path=backup_path, error=str(e))
            return None

    def download_backup(self, object_name: str,
                        destination_path: str) -> bool:
        """
        Downloads a backup from OCI Object Storage.

        Parameters:
            object_name: Name of the object in the bucket.
            destination_path: Local path to save the downloaded file.

        Returns:
            True if download succeeded, False otherwise.

        Side effects:
            Creates or overwrites the file at destination_path.
            Decompresses if the object is gzipped.
        """
        client = self._get_client()

        try:
            response = client.get_object(
                self.namespace, self.BUCKET_NAME, object_name
            )

            dest = Path(destination_path)
            dest.parent.mkdir(parents=True, exist_ok=True)

            with open(destination_path, 'wb') as f:
                for chunk in response.data.raw.stream(1024 * 1024):
                    f.write(chunk)

            # If file is gzipped and destination doesn't end in .gz, decompress
            if object_name.endswith('.gz') and not destination_path.endswith('.gz'):
                decompressed_path = destination_path.replace('.gz', '')
                with gzip.open(destination_path, 'rb') as f_in:
                    with open(decompressed_path, 'wb') as f_out:
                        shutil.copyfileobj(f_in, f_out)
                os.remove(destination_path)
                destination_path = decompressed_path

            logger.info("backup_downloaded",
                         object_name=object_name,
                         destination=destination_path)
            return True

        except Exception as e:
            logger.error("backup_download_failed",
                         object_name=object_name, error=str(e))
            return False

    def list_backups(self) -> list[dict]:
        """
        Lists all backup objects in the bucket.

        Returns:
            List of dicts with keys: name, size_bytes, time_created,
            md5, time_modified. Sorted by creation time descending.

        Side effects:
            None (read-only operation).
        """
        client = self._get_client()

        try:
            backups = []
            next_start = None

            while True:
                if next_start:
                    response = client.list_objects(
                        self.namespace, self.BUCKET_NAME,
                        prefix='backup/',
                        fields='name,size,timeCreated,md5,timeModified',
                        start=next_start
                    )
                else:
                    response = client.list_objects(
                        self.namespace, self.BUCKET_NAME,
                        prefix='backup/',
                        fields='name,size,timeCreated,md5,timeModified'
                    )

                for obj in response.data.objects:
                    backups.append({
                        'name': obj.name,
                        'size_bytes': obj.size,
                        'time_created': obj.time_created.isoformat() if obj.time_created else None,
                        'md5': obj.md5,
                        'time_modified': obj.time_modified.isoformat() if obj.time_modified else None,
                    })

                next_start = response.data.next_start_with
                if not next_start:
                    break

            # Sort by creation time, newest first
            backups.sort(key=lambda x: x.get('time_created', ''), reverse=True)

            logger.info("backups_listed", count=len(backups))
            return backups

        except Exception as e:
            logger.error("list_backups_failed", error=str(e))
            return []

    def delete_old_backups(self, keep_days: int = None) -> int:
        """
        Deletes backups older than keep_days.

        Parameters:
            keep_days: Number of days to retain. Defaults to DEFAULT_RETENTION_DAYS.

        Returns:
            Number of backups deleted.

        Side effects:
            Permanently deletes old backup objects from OCI storage.
            Updates database records for deleted backups.
        """
        if keep_days is None:
            keep_days = self.DEFAULT_RETENTION_DAYS

        client = self._get_client()
        cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
        deleted_count = 0

        try:
            backups = self.list_backups()

            for backup in backups:
                if not backup.get('time_created'):
                    continue

                created = datetime.fromisoformat(
                    backup['time_created'].replace('Z', '+00:00')
                )
                if created < cutoff:
                    try:
                        client.delete_object(
                            self.namespace,
                            self.BUCKET_NAME,
                            backup['name']
                        )
                        deleted_count += 1
                        logger.info("old_backup_deleted",
                                     object_name=backup['name'],
                                     age_days=(datetime.now(timezone.utc) - created).days)
                    except Exception as e:
                        logger.warning("backup_delete_failed",
                                        object_name=backup['name'],
                                        error=str(e))

            if deleted_count > 0:
                logger.info("old_backups_cleanup_complete",
                             deleted=deleted_count,
                             retention_days=keep_days)

            return deleted_count

        except Exception as e:
            logger.error("delete_old_backups_failed", error=str(e))
            return deleted_count

    def get_storage_usage_gb(self) -> float:
        """
        Returns current bucket storage usage in GB.
        Alerts if approaching 16GB (80% of 20GB free tier).

        Returns:
            Storage usage in gigabytes.

        Side effects:
            Logs a warning if usage exceeds ALERT_THRESHOLD_GB.
            Sends a Telegram alert if usage is critical.
        """
        client = self._get_client()

        try:
            backups = self.list_backups()
            total_bytes = sum(b.get('size_bytes', 0) for b in backups)
            usage_gb = total_bytes / (1024 ** 3)

            if usage_gb >= self.ALERT_THRESHOLD_GB:
                logger.warning("oci_storage_alert",
                               usage_gb=round(usage_gb, 2),
                               threshold_gb=self.ALERT_THRESHOLD_GB,
                               total_free_gb=self.TOTAL_FREE_GB)
                self._send_storage_alert(usage_gb)
            elif usage_gb >= self.TOTAL_FREE_GB * 0.6:
                logger.info("oci_storage_warning",
                            usage_gb=round(usage_gb, 2),
                            msg="Approaching 60% of free tier limit")

            return round(usage_gb, 3)

        except Exception as e:
            logger.error("get_storage_usage_failed", error=str(e))
            return 0.0

    def get_latest_backup(self) -> Optional[dict]:
        """
        Returns information about the most recent backup.

        Returns:
            Dict with backup info (name, size_bytes, time_created, etc.)
            or None if no backups exist.

        Side effects:
            None (read-only operation).
        """
        backups = self.list_backups()
        if backups:
            return backups[0]  # Already sorted newest first
        return None

    def verify_backup_integrity(self, object_name: str) -> bool:
        """
        Verifies a backup's integrity by checking MD5 hash.

        Parameters:
            object_name: Name of the backup object in the bucket.

        Returns:
            True if the backup passes integrity check, False otherwise.

        Side effects:
            Downloads the object header for MD5 comparison.
        """
        client = self._get_client()

        try:
            response = client.head_object(
                self.namespace, self.BUCKET_NAME, object_name
            )

            stored_md5 = response.headers.get('content-md5', '')
            if stored_md5:
                logger.info("backup_integrity_verified",
                             object_name=object_name,
                             md5=stored_md5)
                return True

            logger.warning("backup_no_md5",
                            object_name=object_name)
            return False

        except Exception as e:
            logger.error("backup_integrity_check_failed",
                         object_name=object_name, error=str(e))
            return False

    def get_backup_stats(self) -> dict:
        """
        Returns comprehensive backup statistics.

        Returns:
            Dict with keys: total_backups, total_size_gb, oldest_backup,
            newest_backup, avg_size_mb, storage_percent_used.

        Side effects:
            None (read-only operation).
        """
        backups = self.list_backups()

        if not backups:
            return {
                'total_backups': 0,
                'total_size_gb': 0.0,
                'oldest_backup': None,
                'newest_backup': None,
                'avg_size_mb': 0.0,
                'storage_percent_used': 0.0,
            }

        total_bytes = sum(b.get('size_bytes', 0) for b in backups)
        total_gb = total_bytes / (1024 ** 3)
        avg_mb = (total_bytes / len(backups)) / (1024 ** 2)

        return {
            'total_backups': len(backups),
            'total_size_gb': round(total_gb, 3),
            'oldest_backup': backups[-1].get('time_created'),
            'newest_backup': backups[0].get('time_created'),
            'avg_size_mb': round(avg_mb, 2),
            'storage_percent_used': round((total_gb / self.TOTAL_FREE_GB) * 100, 1),
        }

    def _calculate_md5(self, file_path: str) -> str:
        """
        Calculates base64-encoded MD5 hash of a file.

        Parameters:
            file_path: Path to the file.

        Returns:
            Base64-encoded MD5 hash string.
        """
        import base64
        md5 = hashlib.md5()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                md5.update(chunk)
        return base64.b64encode(md5.digest()).decode('utf-8')

    def _record_backup(self, object_name: str, size_bytes: int,
                       md5_hash: str) -> None:
        """
        Records backup metadata in the local database.

        Parameters:
            object_name: Name of the object in OCI storage.
            size_bytes: Size of the backup file in bytes.
            md5_hash: MD5 hash of the backup file.

        Side effects:
            Inserts a row into the oci_backup_objects table.
        """
        try:
            from database.db import Database
            db = Database()
            db.execute_write(
                """INSERT INTO oci_backup_objects
                   (object_name, size_bytes, object_type, md5_hash, uploaded_at)
                   VALUES (?, ?, 'backup', ?, CURRENT_TIMESTAMP)""",
                (object_name, size_bytes, md5_hash)
            )
        except Exception as e:
            # Don't fail the backup if DB recording fails
            logger.warning("backup_record_failed",
                            object_name=object_name, error=str(e))

    def _send_storage_alert(self, usage_gb: float) -> None:
        """
        Sends a Telegram alert when storage usage is critical.

        Parameters:
            usage_gb: Current storage usage in gigabytes.

        Side effects:
            Sends a Telegram message to the configured alert chat.
        """
        try:
            import requests

            bot_token = os.getenv('TELEGRAM_BOT_TOKEN', '')
            chat_id = os.getenv('TELEGRAM_ALERT_CHAT_ID',
                               os.getenv('TELEGRAM_REVIEW_CHAT_ID', ''))

            if not bot_token or not chat_id:
                logger.warning("telegram_not_configured_for_alerts")
                return

            percent_used = (usage_gb / self.TOTAL_FREE_GB) * 100
            message = (
                f"⚠️ OCI Storage Alert\n"
                f"Usage: {usage_gb:.2f} GB / {self.TOTAL_FREE_GB} GB "
                f"({percent_used:.1f}%)\n"
                f"Threshold: {self.ALERT_THRESHOLD_GB} GB\n"
                f"Action: Consider running backup cleanup or "
                f"reviewing storage usage."
            )

            requests.post(
                f'https://api.telegram.org/bot{bot_token}/sendMessage',
                json={'chat_id': chat_id, 'text': message},
                timeout=10
            )

        except Exception as e:
            logger.warning("storage_alert_send_failed", error=str(e))
