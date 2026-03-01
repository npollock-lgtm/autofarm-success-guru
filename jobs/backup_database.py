"""
Backup Database — runs daily at 02:30 UTC.

Creates a compressed SQLite backup and uploads it to OCI Object Storage
with 14-day retention.
"""

import asyncio
import gzip
import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from modules.infrastructure.logging_config import setup_logging
from modules.storage.oci_storage import OCIStorage
from database.db import Database

logger = logging.getLogger("autofarm.jobs.backup_database")

DB_PATH = Path("data/autofarm.db")
BACKUP_DIR = Path("data/backups")
RETENTION_DAYS = 14


async def main() -> None:
    """Create and upload a database backup.

    Side Effects
    ------------
    - Creates a compressed backup file locally.
    - Uploads to OCI Object Storage.
    - Removes local backups older than RETENTION_DAYS.
    """
    setup_logging()
    logger.info("Starting backup_database job")

    db = Database()
    await db.initialize()

    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_name = f"autofarm_backup_{timestamp}.db.gz"
        backup_path = BACKUP_DIR / backup_name

        # 1. Create backup via SQLite VACUUM INTO (or copy)
        if DB_PATH.exists():
            temp_backup = BACKUP_DIR / f"temp_{timestamp}.db"

            # Use SQLite backup API
            await db.execute(f"VACUUM INTO '{temp_backup}'")

            # Compress
            with open(temp_backup, "rb") as f_in:
                with gzip.open(backup_path, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)

            temp_backup.unlink(missing_ok=True)
            size_mb = backup_path.stat().st_size / (1024 * 1024)
            logger.info("Backup created: %s (%.1f MB)", backup_name, size_mb)

        # 2. Upload to OCI
        try:
            storage = OCIStorage()
            await storage.upload_file(
                str(backup_path),
                f"backups/{backup_name}",
            )
            logger.info("Backup uploaded to OCI: backups/%s", backup_name)
        except Exception as oci_exc:
            logger.warning("OCI upload failed (non-fatal): %s", oci_exc)

        # 3. Clean up old local backups
        cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
        removed = 0
        for f in BACKUP_DIR.glob("autofarm_backup_*.db.gz"):
            try:
                mtime = datetime.fromtimestamp(
                    f.stat().st_mtime, tz=timezone.utc
                )
                if mtime < cutoff:
                    f.unlink()
                    removed += 1
            except OSError:
                pass

        if removed:
            logger.info("Removed %d old backups", removed)

    except Exception as exc:
        logger.error("backup_database error: %s", exc, exc_info=True)
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
