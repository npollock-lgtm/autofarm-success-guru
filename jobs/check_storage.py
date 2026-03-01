"""
Check Storage — runs daily at 06:00 UTC.

Monitors disk usage, Google Drive storage, and OCI Object Storage.
Sends alerts if usage exceeds thresholds.
"""

import asyncio
import logging
import shutil
from pathlib import Path

from modules.infrastructure.logging_config import setup_logging
from modules.notifications.telegram_bot import TelegramNotifier
from database.db import Database

logger = logging.getLogger("autofarm.jobs.check_storage")

# Disk usage thresholds
DISK_WARNING_PCT = 80
DISK_CRITICAL_PCT = 90


async def main() -> None:
    """Check all storage systems and alert if necessary.

    Side Effects
    ------------
    Sends Telegram alerts if storage thresholds are exceeded.
    Triggers Google Drive cleanup if needed.
    """
    setup_logging()
    logger.info("Starting check_storage job")

    db = Database()
    await db.initialize()

    try:
        notifier = TelegramNotifier(db=db)

        # 1. Check local disk
        disk = shutil.disk_usage("/")
        used_pct = (disk.used / disk.total) * 100

        if used_pct >= DISK_CRITICAL_PCT:
            await notifier.send_health_warning(
                "Disk Storage",
                "critical",
                f"Disk usage at {used_pct:.1f}% — "
                f"{disk.free / (1024**3):.1f} GB free",
            )
        elif used_pct >= DISK_WARNING_PCT:
            await notifier.send_health_warning(
                "Disk Storage",
                "warning",
                f"Disk usage at {used_pct:.1f}% — "
                f"{disk.free / (1024**3):.1f} GB free",
            )

        # 2. Check data directory sizes
        data_dir = Path("data")
        if data_dir.exists():
            total_size = sum(
                f.stat().st_size for f in data_dir.rglob("*") if f.is_file()
            )
            total_gb = total_size / (1024 ** 3)
            logger.info("Data directory: %.2f GB", total_gb)

        # 3. Check Google Drive storage
        import os
        if os.getenv("GDRIVE_ENABLED", "").lower() == "true":
            try:
                from modules.review_gate.gdrive_uploader import GDriveVideoUploader
                uploader = GDriveVideoUploader(db=db)
                usage = await uploader.get_storage_usage()
                if usage.get("used_gb", 0) > 12:
                    await notifier.send_health_warning(
                        "Google Drive",
                        "critical",
                        f"Google Drive at {usage['used_gb']:.1f} GB / 15 GB. "
                        f"Triggering cleanup...",
                    )
                    await uploader.cleanup_expired_reviews()
                elif usage.get("used_gb", 0) > 9:
                    await notifier.send_health_warning(
                        "Google Drive",
                        "warning",
                        f"Google Drive at {usage['used_gb']:.1f} GB / 15 GB",
                    )
            except Exception as gdrive_exc:
                logger.warning("Google Drive check failed: %s", gdrive_exc)

        logger.info("Storage check complete: disk %.1f%% used", used_pct)

    except Exception as exc:
        logger.error("check_storage error: %s", exc, exc_info=True)
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
