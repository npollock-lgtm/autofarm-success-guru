"""
Add Brand — interactive CLI to add a new brand to the AutoFarm system.

Flow:
1. Enter brand name + niche description.
2. System calls LLM to generate full brand config.
3. Shows generated config for human review/editing.
4. On confirmation: creates DB records, directories, assets.
5. Integrates brand into full pipeline.

Usage::

    python scripts/add_brand.py
"""

import asyncio
import json
import sys
from pathlib import Path


async def main() -> None:
    """Interactive brand creation flow.

    Side Effects
    ------------
    Creates brand record in database.
    Updates ``config/brands.json``.
    Creates brand-specific directories.
    """
    from database.db import Database
    from modules.ai_brain.brand_generator import BrandConfigGenerator
    from modules.ai_brain.llm_router import LLMRouter

    print("\n  ADD NEW BRAND TO AUTOFARM")
    print("=" * 50)

    # 1. Get input
    brand_name = input("Brand name (snake_case, e.g. 'fitness_success_guru'): ").strip()
    if not brand_name:
        print("ERROR: Brand name required")
        sys.exit(1)

    niche = input("Niche description (e.g. 'fitness and workout motivation'): ").strip()
    if not niche:
        print("ERROR: Niche description required")
        sys.exit(1)

    dry_run = "--dry-run" in sys.argv

    print(f"\nGenerating brand config for '{brand_name}' ({niche})...")

    db = Database()
    await db.initialize()

    try:
        llm = LLMRouter(db=db)
        generator = BrandConfigGenerator(db=db, llm_router=llm)

        # 2. Generate config via LLM
        config = await generator.generate_brand_config(brand_name, niche)
        if not config:
            print("ERROR: Failed to generate brand config")
            sys.exit(1)

        # 3. Show for review
        print("\n  GENERATED BRAND CONFIG")
        print("=" * 50)
        print(json.dumps(config, indent=2, default=str))

        if dry_run:
            print("\n[DRY RUN] Config generated but not saved.")
            return

        # 4. Confirm
        confirm = input("\nSave this config? (y/N): ").strip().lower()
        if confirm != "y":
            print("Cancelled.")
            return

        # 5. Create database record
        await db.execute(
            """
            INSERT INTO brands (id, config, status, created_at)
            VALUES (?, ?, 'active', datetime('now'))
            """,
            (brand_name, json.dumps(config)),
        )

        # 6. Update brands.json
        brands_path = Path("config/brands.json")
        if brands_path.exists():
            brands = json.loads(brands_path.read_text())
        else:
            brands = {}

        brands[brand_name] = config
        brands_path.write_text(json.dumps(brands, indent=2))

        # 7. Create directories
        dirs = [
            f"data/videos/{brand_name}",
            f"data/audio/{brand_name}",
            f"data/thumbnails/{brand_name}",
            f"data/backgrounds/{brand_name}",
        ]
        for d in dirs:
            Path(d).mkdir(parents=True, exist_ok=True)

        print(f"\n  Brand '{brand_name}' added successfully!")
        print(f"  Next: python scripts/add_account.py to register platform accounts")

    except Exception as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
