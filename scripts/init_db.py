"""
Init DB — initialises the SQLite database from ``database/schema.sql``.

Creates the database file, applies all table definitions, enables
WAL mode, and pre-populates initial brand and account records.

Usage::

    python scripts/init_db.py
"""

import asyncio
import json
import sys
from pathlib import Path


async def main() -> None:
    """Initialise the AutoFarm database.

    Side Effects
    ------------
    Creates ``data/autofarm.db`` with all tables from schema.sql.
    Enables WAL mode.
    Pre-populates brands table from ``config/brands.json``.
    Pre-populates accounts with ``pending_setup`` status.
    """
    print("\n  INITIALISING DATABASE")
    print("=" * 50)

    # Ensure data directory exists
    Path("data").mkdir(parents=True, exist_ok=True)

    db_path = Path("data/autofarm.db")
    schema_path = Path("database/schema.sql")

    if not schema_path.exists():
        print("  ERROR: database/schema.sql not found")
        sys.exit(1)

    from database.db import Database

    db = Database()
    await db.initialize()

    try:
        # 1. Apply schema
        schema_sql = schema_path.read_text()
        statements = [
            s.strip() for s in schema_sql.split(";") if s.strip()
        ]

        applied = 0
        for stmt in statements:
            if stmt:
                await db.execute(stmt)
                applied += 1

        print(f"  PASS  Applied {applied} SQL statements")

        # 2. Enable WAL mode
        await db.execute("PRAGMA journal_mode=WAL")
        print(f"  PASS  WAL mode enabled")

        # 3. Pre-populate brands
        brands_path = Path("config/brands.json")
        if brands_path.exists():
            brands = json.loads(brands_path.read_text())
            for brand_id, config in brands.items():
                await db.execute(
                    """
                    INSERT OR IGNORE INTO brands (id, config, status, created_at)
                    VALUES (?, ?, 'active', datetime('now'))
                    """,
                    (brand_id, json.dumps(config)),
                )
            print(f"  PASS  {len(brands)} brands loaded")
        else:
            print("  SKIP  config/brands.json not found")

        # 4. Pre-populate accounts (pending_setup for TikTok + Snapchat)
        platforms = ["tiktok", "instagram", "facebook", "youtube", "snapchat"]
        brand_rows = await db.fetch_all("SELECT id FROM brands")
        account_count = 0

        for brand in brand_rows:
            brand_id = brand["id"]
            for platform in platforms:
                await db.execute(
                    """
                    INSERT OR IGNORE INTO accounts
                        (brand_id, platform, status, created_at)
                    VALUES (?, ?, 'pending_setup', datetime('now'))
                    """,
                    (brand_id, platform),
                )
                account_count += 1

        print(f"  PASS  {account_count} account slots created (pending_setup)")

        # 5. Verify
        table_count = await db.fetch_one(
            """
            SELECT COUNT(*) AS cnt
            FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            """
        )
        print(f"  PASS  {table_count['cnt']} tables in database")

        print(f"\n  Database initialised: {db_path}")

    except Exception as exc:
        print(f"  ERROR: {exc}")
        sys.exit(1)
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
