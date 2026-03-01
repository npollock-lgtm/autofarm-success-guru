"""
Test Proxy Routing — verifies all 6 brand proxies route through
the correct public IPs.

Run during setup and included in ``test_pipeline.py`` as Test #25.

Usage::

    python scripts/test_proxy_routing.py
"""

import asyncio
import sys


async def main() -> None:
    """Verify IP routing for all brands.

    Prints pass/fail for each brand and exits with code 1 if any fail.
    """
    from database.db import Database
    from modules.network.ip_router import BrandIPRouter

    db = Database()
    await db.initialize()

    try:
        router = BrandIPRouter(db=db)
        results = await router.verify_all_brands()

        print("\n  IP ROUTING VERIFICATION")
        print("=" * 55)
        all_passed = True
        for r in results:
            if r.get("verified"):
                print(
                    f"  PASS {r['brand_id']:<35} -> {r.get('actual_source_ip', '?')}"
                )
            else:
                print(
                    f"  FAIL {r['brand_id']:<35} -> FAILED: {r.get('error', 'unknown')}"
                )
                all_passed = False

        print("=" * 55)
        if all_passed:
            print("All 6 brand proxies routing correctly.\n")
        else:
            print("Some proxies failed. Check Squid service status on proxy-vm.\n")
            sys.exit(1)
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
