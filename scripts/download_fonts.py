"""
Download Fonts — fetches brand-specific fonts for thumbnail generation.

Downloads free fonts from Google Fonts and places them in
``data/fonts/{brand_id}/``.

Usage::

    python scripts/download_fonts.py
"""

import os
import sys
from pathlib import Path

import requests

# Brand font mappings (Google Fonts)
BRAND_FONTS = {
    "human_success_guru": {
        "primary": "Montserrat-Bold",
        "url": "https://fonts.google.com/download?family=Montserrat",
    },
    "wealth_success_guru": {
        "primary": "Playfair-Display-Bold",
        "url": "https://fonts.google.com/download?family=Playfair+Display",
    },
    "zen_success_guru": {
        "primary": "Lato-Regular",
        "url": "https://fonts.google.com/download?family=Lato",
    },
    "social_success_guru": {
        "primary": "Poppins-SemiBold",
        "url": "https://fonts.google.com/download?family=Poppins",
    },
    "habits_success_guru": {
        "primary": "Roboto-Bold",
        "url": "https://fonts.google.com/download?family=Roboto",
    },
    "relationships_success_guru": {
        "primary": "Open-Sans-Bold",
        "url": "https://fonts.google.com/download?family=Open+Sans",
    },
}

FONTS_DIR = Path("data/fonts")


def main() -> None:
    """Download fonts for all brands.

    Side Effects
    ------------
    Creates ``data/fonts/{brand_id}/`` directories.
    Downloads font ZIP files and extracts TTF files.
    """
    print("\n  DOWNLOADING BRAND FONTS")
    print("=" * 50)

    FONTS_DIR.mkdir(parents=True, exist_ok=True)

    for brand_id, font_info in BRAND_FONTS.items():
        brand_dir = FONTS_DIR / brand_id
        brand_dir.mkdir(parents=True, exist_ok=True)

        # Check if already downloaded
        existing_ttf = list(brand_dir.glob("*.ttf"))
        if existing_ttf:
            print(f"  SKIP  {brand_id} — {len(existing_ttf)} fonts already present")
            continue

        print(f"  Downloading fonts for {brand_id}...")
        try:
            resp = requests.get(font_info["url"], timeout=60)
            if resp.status_code == 200:
                zip_path = brand_dir / "font.zip"
                zip_path.write_bytes(resp.content)

                # Extract TTF files
                import zipfile
                with zipfile.ZipFile(zip_path, "r") as zf:
                    for name in zf.namelist():
                        if name.lower().endswith(".ttf"):
                            zf.extract(name, brand_dir)

                zip_path.unlink()
                ttf_count = len(list(brand_dir.rglob("*.ttf")))
                print(f"  PASS  {brand_id} — {ttf_count} TTF files extracted")
            else:
                print(f"  FAIL  {brand_id} — HTTP {resp.status_code}")
        except Exception as exc:
            print(f"  FAIL  {brand_id} — {exc}")
            # Create a fallback: copy system default font if available
            print(f"         Will use system default font as fallback")

    print("\nFont download complete.")


if __name__ == "__main__":
    main()
