"""
Cleanup Orphans — runs daily at 04:00 UTC.

Removes orphaned files: partial video assemblies, temp audio files,
stale thumbnails, and any other temporary artifacts left by failed
pipeline runs.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from modules.infrastructure.logging_config import setup_logging
from database.db import Database

logger = logging.getLogger("autofarm.jobs.cleanup_orphans")

# Directories to scan for orphaned files
CLEANUP_DIRS = [
    Path("data/videos/temp"),
    Path("data/audio/temp"),
    Path("data/thumbnails/temp"),
    Path("data/backgrounds/temp"),
]

# Max age for temp files before deletion (hours)
MAX_AGE_HOURS = 24


async def main() -> None:
    """Remove orphaned temporary files.

    Side Effects
    ------------
    Deletes temp files older than MAX_AGE_HOURS.
    Logs total space recovered.
    """
    setup_logging()
    logger.info("Starting cleanup_orphans job")

    db = Database()
    await db.initialize()

    try:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
        total_removed = 0
        total_bytes = 0

        for dir_path in CLEANUP_DIRS:
            if not dir_path.exists():
                continue

            for f in dir_path.rglob("*"):
                if not f.is_file():
                    continue
                try:
                    mtime = datetime.fromtimestamp(
                        f.stat().st_mtime, tz=timezone.utc
                    )
                    if mtime < cutoff:
                        size = f.stat().st_size
                        f.unlink()
                        total_removed += 1
                        total_bytes += size
                except OSError as exc:
                    logger.warning("Failed to remove %s: %s", f, exc)

        # Also clean up orphaned video files not in database
        videos_dir = Path("data/videos")
        if videos_dir.exists():
            for f in videos_dir.glob("*.mp4"):
                try:
                    mtime = datetime.fromtimestamp(
                        f.stat().st_mtime, tz=timezone.utc
                    )
                    if mtime < cutoff:
                        # Check if file is referenced in database
                        row = await db.fetch_one(
                            "SELECT id FROM videos WHERE video_path = ?",
                            (str(f),),
                        )
                        if not row:
                            size = f.stat().st_size
                            f.unlink()
                            total_removed += 1
                            total_bytes += size
                except (OSError, Exception) as exc:
                    logger.warning("Error checking %s: %s", f, exc)

        recovered_mb = total_bytes / (1024 * 1024)
        logger.info(
            "Orphan cleanup: removed %d files, recovered %.1f MB",
            total_removed, recovered_mb,
        )

    except Exception as exc:
        logger.error("cleanup_orphans error: %s", exc, exc_info=True)
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
