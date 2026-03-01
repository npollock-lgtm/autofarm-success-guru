"""
Validate Config — runs daily at 05:30 UTC.

Runs the full configuration validator to ensure all settings, brand
configs, API credentials, and file paths are correct.
"""

import asyncio
import logging

from modules.infrastructure.logging_config import setup_logging
from modules.infrastructure.config_validator import ConfigValidator
from modules.notifications.telegram_bot import TelegramNotifier
from database.db import Database

logger = logging.getLogger("autofarm.jobs.validate_config")


async def main() -> None:
    """Run configuration validation and alert on issues.

    Side Effects
    ------------
    Sends Telegram alert if validation fails.
    Logs all validation results.
    """
    setup_logging()
    logger.info("Starting validate_config job")

    db = Database()
    await db.initialize()

    try:
        validator = ConfigValidator(db=db)
        result = await validator.validate_all()

        errors = result.get("errors", [])
        warnings = result.get("warnings", [])

        if errors:
            notifier = TelegramNotifier(db=db)
            error_text = "\n".join(f"\u274c {e}" for e in errors[:10])
            await notifier.send_error_alert(
                "config_validation",
                f"Config validation found {len(errors)} errors:\n{error_text}",
            )

        if warnings:
            logger.warning(
                "Config validation warnings: %s",
                "; ".join(warnings[:10]),
            )

        logger.info(
            "Config validation: %d errors, %d warnings",
            len(errors), len(warnings),
        )

    except Exception as exc:
        logger.error("validate_config error: %s", exc, exc_info=True)
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
