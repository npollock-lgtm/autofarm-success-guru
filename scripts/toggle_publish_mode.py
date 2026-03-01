"""
Toggle Publish Mode — switch between dry-run and live publishing
for specific brands or the entire system.

Modes:
- ``dry_run``: Videos are assembled and reviewed but never published.
- ``live``: Full pipeline including real publishing.
- ``review_only``: Publish only after explicit human approval.

Usage::

    python scripts/toggle_publish_mode.py
    python scripts/toggle_publish_mode.py --brand human_success_guru --mode live
    python scripts/toggle_publish_mode.py --all --mode dry_run
"""

import argparse
import asyncio
import json
import sys


async def main() -> None:
    """Toggle publish mode for brands.

    Side Effects
    ------------
    Updates ``publish_mode_overrides`` table.
    """
    from database.db import Database

    parser = argparse.ArgumentParser(description="Toggle publish mode")
    parser.add_argument("--brand", help="Specific brand to toggle")
    parser.add_argument("--all", action="store_true", help="Toggle all brands")
    parser.add_argument(
        "--mode",
        choices=["dry_run", "live", "review_only"],
        help="Publishing mode",
    )
    parser.add_argument("--list", action="store_true", help="List current modes")
    args = parser.parse_args()

    db = Database()
    await db.initialize()

    try:
        if args.list or (not args.brand and not args.all):
            # Show current modes
            overrides = await db.fetch_all(
                """
                SELECT brand_id, platform, mode, updated_at
                FROM publish_mode_overrides
                ORDER BY brand_id, platform
                """
            )

            print("\n  PUBLISH MODE STATUS")
            print("=" * 60)

            if not overrides:
                print("  No overrides set — all brands using default mode (dry_run)")
            else:
                for o in overrides:
                    mode_icon = {
                        "live": "LIVE",
                        "dry_run": "DRY",
                        "review_only": "REVIEW",
                    }.get(o["mode"], o["mode"])
                    print(
                        f"  {o['brand_id']:<30} {o['platform'] or 'all':<12} {mode_icon}"
                    )

            print("=" * 60)
            return

        if not args.mode:
            print("  ERROR: --mode required (dry_run, live, review_only)")
            sys.exit(1)

        brands_to_update = []
        if args.all:
            rows = await db.fetch_all("SELECT id FROM brands")
            brands_to_update = [r["id"] for r in rows]
        elif args.brand:
            brands_to_update = [args.brand]
        else:
            print("  ERROR: specify --brand or --all")
            sys.exit(1)

        platforms = ["tiktok", "instagram", "facebook", "youtube", "snapchat"]

        for brand_id in brands_to_update:
            for platform in platforms:
                await db.execute(
                    """
                    INSERT INTO publish_mode_overrides
                        (brand_id, platform, mode, updated_at)
                    VALUES (?, ?, ?, datetime('now'))
                    ON CONFLICT(brand_id, platform) DO UPDATE SET
                        mode = excluded.mode,
                        updated_at = excluded.updated_at
                    """,
                    (brand_id, platform, args.mode),
                )

            print(f"  {brand_id} -> {args.mode}")

        print(f"\n  Updated {len(brands_to_update)} brands to '{args.mode}' mode")

    except Exception as exc:
        print(f"  ERROR: {exc}")
        sys.exit(1)
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
