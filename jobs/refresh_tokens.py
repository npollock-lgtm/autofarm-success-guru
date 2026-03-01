"""
Refresh Tokens — runs daily at 04:45 UTC (15 min before first publish window).

Refreshes OAuth tokens for all brand × platform accounts to ensure
publishing will not fail due to expired credentials.
"""

import asyncio
import logging

from modules.infrastructure.logging_config import setup_logging
from account_manager.token_refresher import TokenRefresher
from database.db import Database

logger = logging.getLogger("autofarm.jobs.refresh_tokens")


async def main() -> None:
    """Refresh all OAuth tokens that are expiring soon.

    Side Effects
    ------------
    Updates credentials in ``accounts`` table.
    Logs failures and sends alerts for tokens that cannot be refreshed.
    """
    setup_logging()
    logger.info("Starting refresh_tokens job")

    db = Database()
    await db.initialize()

    try:
        refresher = TokenRefresher(db=db)
        result = await refresher.refresh_all_tokens()
        logger.info(
            "Token refresh: %d refreshed, %d failed",
            result.get("refreshed", 0), result.get("failed", 0),
        )
        if result.get("failed", 0) > 0:
            from modules.notifications.telegram_bot import TelegramNotifier
            notifier = TelegramNotifier(db=db)
            await notifier.send_error_alert(
                "token_refresh_failure",
                f"{result['failed']} tokens failed to refresh",
                details=result.get("errors"),
            )
    except Exception as exc:
        logger.error("refresh_tokens error: %s", exc, exc_info=True)
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
