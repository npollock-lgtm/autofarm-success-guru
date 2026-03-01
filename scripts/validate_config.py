"""
Validate Config — startup configuration checker.

Verifies all required environment variables, API keys, file paths,
brand configs, and database connectivity before the system runs.

Usage::

    python scripts/validate_config.py
"""

import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


async def main() -> None:
    """Run all configuration validation checks.

    Prints pass/fail for each check and exits with code 1 if critical
    checks fail.
    """
    print("\n  CONFIGURATION VALIDATION")
    print("=" * 55)

    errors = []
    warnings = []
    passed = 0

    # 1. Required environment variables
    required_env = [
        "OLLAMA_HOST",
        "ENCRYPTION_KEY",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_REVIEW_CHAT_ID",
    ]
    optional_env = [
        "GROQ_API_KEY",
        "PEXELS_API_KEY",
        "PIXABAY_API_KEY",
        "SMTP_HOST",
        "SMTP_USER",
        "SMTP_PASS",
        "GDRIVE_ENABLED",
        "PROXY_VM_PUBLIC_IP",
    ]

    for var in required_env:
        if os.getenv(var):
            print(f"  PASS  env.{var}")
            passed += 1
        else:
            print(f"  FAIL  env.{var} — not set")
            errors.append(f"Missing required env: {var}")

    for var in optional_env:
        if os.getenv(var):
            print(f"  PASS  env.{var}")
            passed += 1
        else:
            print(f"  SKIP  env.{var} — optional, not set")
            warnings.append(f"Optional env not set: {var}")

    # 2. Config files
    config_files = [
        "config/brands.json",
        "config/platforms.json",
        "config/settings.py",
        "config/youtube_projects.json",
    ]
    for cf in config_files:
        if Path(cf).exists():
            print(f"  PASS  {cf}")
            passed += 1
        else:
            print(f"  FAIL  {cf} — not found")
            errors.append(f"Missing config: {cf}")

    # 3. Validate brands.json structure
    brands_path = Path("config/brands.json")
    if brands_path.exists():
        try:
            brands = json.loads(brands_path.read_text())
            if isinstance(brands, dict) and len(brands) >= 1:
                print(f"  PASS  brands.json — {len(brands)} brands configured")
                passed += 1
            else:
                print(f"  FAIL  brands.json — empty or invalid")
                errors.append("brands.json is empty or invalid")
        except json.JSONDecodeError as exc:
            print(f"  FAIL  brands.json — parse error: {exc}")
            errors.append(f"brands.json parse error: {exc}")

    # 4. Database
    db_path = Path("data/autofarm.db")
    if db_path.exists():
        print(f"  PASS  database exists ({db_path})")
        passed += 1

        try:
            from database.db import Database
            db = Database()
            await db.initialize()
            row = await db.fetch_one("SELECT COUNT(*) AS cnt FROM brands")
            brand_count = row["cnt"] if row else 0
            print(f"  PASS  database readable — {brand_count} brands in DB")
            passed += 1
            await db.close()
        except Exception as exc:
            print(f"  FAIL  database connectivity — {exc}")
            errors.append(f"Database error: {exc}")
    else:
        print(f"  SKIP  database not yet created — run scripts/init_db.py first")
        warnings.append("Database not created yet")

    # 5. Required directories
    required_dirs = [
        "data", "data/videos", "data/audio", "data/thumbnails",
        "data/backgrounds", "data/backups", "logs",
    ]
    for d in required_dirs:
        if Path(d).is_dir():
            print(f"  PASS  dir {d}/")
            passed += 1
        else:
            print(f"  WARN  dir {d}/ — not found")
            warnings.append(f"Directory not found: {d}")

    # 6. FFmpeg
    import shutil
    if shutil.which("ffmpeg"):
        print(f"  PASS  ffmpeg available")
        passed += 1
    else:
        print(f"  FAIL  ffmpeg — not found in PATH")
        errors.append("ffmpeg not installed")

    # 7. Schema file
    schema_path = Path("database/schema.sql")
    if schema_path.exists():
        content = schema_path.read_text()
        table_count = content.count("CREATE TABLE")
        print(f"  PASS  schema.sql — {table_count} tables defined")
        passed += 1
    else:
        print(f"  FAIL  database/schema.sql — not found")
        errors.append("schema.sql not found")

    # Summary
    print("\n" + "=" * 55)
    print(f"  Results: {passed} passed, {len(errors)} errors, {len(warnings)} warnings")

    if errors:
        print(f"\n  ERRORS:")
        for e in errors:
            print(f"    - {e}")

    if warnings:
        print(f"\n  WARNINGS:")
        for w in warnings:
            print(f"    - {w}")

    print()
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
