"""
Add Account — interactive CLI to register platform credentials
for a brand.

Encrypts and stores OAuth tokens in the ``accounts`` table using
``CredentialManager``.

Usage::

    python scripts/add_account.py
"""

import asyncio
import json
import sys


async def main() -> None:
    """Interactive account registration flow.

    Side Effects
    ------------
    Creates account record in database.
    Encrypts and stores credentials.
    """
    from database.db import Database
    from database.credential_manager import CredentialManager

    print("\n  ADD PLATFORM ACCOUNT")
    print("=" * 50)

    db = Database()
    await db.initialize()

    try:
        cred_manager = CredentialManager(db=db)

        # Get brand
        brands = await db.fetch_all("SELECT id FROM brands ORDER BY id")
        if not brands:
            print("  No brands found. Run scripts/add_brand.py first.")
            return

        print("\n  Available brands:")
        for i, b in enumerate(brands, 1):
            print(f"    {i}. {b['id']}")

        brand_idx = int(input("\n  Select brand number: ")) - 1
        brand_id = brands[brand_idx]["id"]

        # Get platform
        platforms = ["tiktok", "instagram", "facebook", "youtube", "snapchat"]
        print("\n  Available platforms:")
        for i, p in enumerate(platforms, 1):
            print(f"    {i}. {p}")

        plat_idx = int(input("\n  Select platform number: ")) - 1
        platform = platforms[plat_idx]

        # Get credentials
        print(f"\n  Enter credentials for {brand_id}/{platform}:")
        username = input("  Username/Page ID: ").strip()
        access_token = input("  Access Token: ").strip()
        refresh_token = input("  Refresh Token (or press Enter to skip): ").strip()

        credentials = {
            "access_token": access_token,
            "username": username,
        }
        if refresh_token:
            credentials["refresh_token"] = refresh_token

        # Platform-specific fields
        if platform == "youtube":
            channel_id = input("  Channel ID: ").strip()
            if channel_id:
                credentials["channel_id"] = channel_id
        elif platform == "facebook":
            page_id = input("  Page ID: ").strip()
            if page_id:
                credentials["page_id"] = page_id
        elif platform == "instagram":
            ig_user_id = input("  Instagram User ID: ").strip()
            if ig_user_id:
                credentials["ig_user_id"] = ig_user_id

        # Store
        await cred_manager.store_credentials(
            brand_id, platform, credentials
        )

        # Create/update account record
        await db.execute(
            """
            INSERT INTO accounts
                (brand_id, platform, username, status, created_at)
            VALUES (?, ?, ?, 'active', datetime('now'))
            ON CONFLICT(brand_id, platform) DO UPDATE SET
                username = excluded.username,
                status = 'active'
            """,
            (brand_id, platform, username),
        )

        print(f"\n  Account registered: {brand_id}/{platform}")
        print(f"  Credentials encrypted and stored.")

    except (ValueError, IndexError):
        print("  Invalid selection")
        sys.exit(1)
    except Exception as exc:
        print(f"  ERROR: {exc}")
        sys.exit(1)
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
