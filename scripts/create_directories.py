"""
Create Directories — creates all required data, log, and temp directories.

Run during initial setup or when adding new brands.

Usage::

    python scripts/create_directories.py
"""

import json
from pathlib import Path

BRANDS = [
    "human_success_guru",
    "wealth_success_guru",
    "zen_success_guru",
    "social_success_guru",
    "habits_success_guru",
    "relationships_success_guru",
]

# Try loading brands from config if it exists
try:
    brands_config = Path("config/brands.json")
    if brands_config.exists():
        loaded = json.loads(brands_config.read_text())
        if isinstance(loaded, dict):
            BRANDS = list(loaded.keys())
except Exception:
    pass


def main() -> None:
    """Create all required directories for the AutoFarm system.

    Side Effects
    ------------
    Creates directory trees under ``data/``, ``logs/``, and ``data/*/temp/``.
    """
    print("\n  CREATING DIRECTORIES")
    print("=" * 50)

    # Base directories
    base_dirs = [
        "data",
        "data/backups",
        "data/fonts",
        "data/audio_cache",
        "data/audio_cache/trending",
        "logs",
    ]

    # Per-type directories
    type_dirs = ["videos", "audio", "thumbnails", "backgrounds"]

    # Per-brand directories
    brand_dirs = []
    for brand_id in BRANDS:
        for type_dir in type_dirs:
            brand_dirs.append(f"data/{type_dir}/{brand_id}")
        # Temp directories
        brand_dirs.append(f"data/videos/{brand_id}/temp")
        brand_dirs.append(f"data/audio/{brand_id}/temp")

    # Temp directories (global)
    temp_dirs = [
        "data/videos/temp",
        "data/audio/temp",
        "data/thumbnails/temp",
        "data/backgrounds/temp",
    ]

    all_dirs = base_dirs + brand_dirs + temp_dirs
    created = 0

    for d in all_dirs:
        p = Path(d)
        if not p.exists():
            p.mkdir(parents=True, exist_ok=True)
            print(f"  CREATED  {d}/")
            created += 1
        else:
            print(f"  EXISTS   {d}/")

    print(f"\n  {created} directories created, {len(all_dirs) - created} already existed")


if __name__ == "__main__":
    main()
