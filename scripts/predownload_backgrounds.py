"""
Pre-download Backgrounds — fetches initial background video clips
for each brand from Pexels and Pixabay.

Populates the background library so the first content generation
cycle doesn't have to wait for downloads.

Usage::

    python scripts/predownload_backgrounds.py
"""

import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()


async def main() -> None:
    """Pre-download background clips for all brands.

    Side Effects
    ------------
    Downloads video clips to ``data/backgrounds/{brand_id}/``.
    Updates ``background_library`` table.
    """
    print("\n  PRE-DOWNLOADING BACKGROUND CLIPS")
    print("=" * 55)

    from database.db import Database
    from modules.content_forge.background_library import BackgroundManager

    db = Database()
    await db.initialize()

    try:
        manager = BackgroundManager(db=db)
        brands = await db.fetch_all("SELECT id FROM brands")

        if not brands:
            print("  No brands found. Add brands first with scripts/add_brand.py")
            return

        total_downloaded = 0
        for brand in brands:
            brand_id = brand["id"]
            print(f"\n  Downloading backgrounds for {brand_id}...")

            try:
                result = await manager.predownload_for_brand(brand_id, count=5)
                downloaded = result.get("downloaded", 0)
                total_downloaded += downloaded
                print(f"  PASS  {brand_id} — {downloaded} clips downloaded")
            except Exception as exc:
                print(f"  FAIL  {brand_id} — {exc}")

        print(f"\n  Total: {total_downloaded} background clips downloaded")

    except Exception as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
