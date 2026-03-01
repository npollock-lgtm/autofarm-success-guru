"""
List Accounts — displays all registered platform accounts and their status.

Usage::

    python scripts/list_accounts.py
"""

import asyncio
import sys


async def main() -> None:
    """Display all registered accounts in a formatted table."""
    from database.db import Database

    db = Database()
    await db.initialize()

    try:
        accounts = await db.fetch_all(
            """
            SELECT brand_id, platform, username, status, created_at
            FROM accounts
            ORDER BY brand_id, platform
            """
        )

        print("\n  REGISTERED ACCOUNTS")
        print("=" * 80)
        print(
            f"  {'Brand':<30} {'Platform':<12} {'Username':<20} {'Status':<10}"
        )
        print("-" * 80)

        if not accounts:
            print("  No accounts registered yet.")
            print("  Run: python scripts/add_account.py")
        else:
            for acc in accounts:
                status_icon = {
                    "active": "OK",
                    "pending_setup": "PENDING",
                    "suspended": "SUSPENDED",
                    "expired": "EXPIRED",
                }.get(acc["status"], acc["status"])

                print(
                    f"  {acc['brand_id']:<30} "
                    f"{acc['platform']:<12} "
                    f"{acc['username'] or '—':<20} "
                    f"{status_icon:<10}"
                )

        print("=" * 80)
        print(f"  Total: {len(accounts)} accounts")

        # Summary by status
        if accounts:
            active = sum(1 for a in accounts if a["status"] == "active")
            pending = sum(1 for a in accounts if a["status"] == "pending_setup")
            print(f"  Active: {active}, Pending Setup: {pending}")

        print()

    except Exception as exc:
        print(f"  ERROR: {exc}")
        sys.exit(1)
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
