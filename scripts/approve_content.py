"""
Approve Content — CLI tool to approve or reject pending reviews.

For use when Telegram and email are unavailable.

Usage::

    python scripts/approve_content.py
    python scripts/approve_content.py --approve TOKEN
    python scripts/approve_content.py --reject TOKEN
"""

import argparse
import asyncio
import sys


async def main() -> None:
    """Interactive or CLI-based review approval.

    Side Effects
    ------------
    Updates review status in database.
    Creates publish jobs for approved content.
    """
    from database.db import Database
    from modules.review_gate.approval_tracker import ApprovalTracker

    parser = argparse.ArgumentParser(description="Approve or reject content")
    parser.add_argument("--approve", metavar="TOKEN", help="Approve by token")
    parser.add_argument("--reject", metavar="TOKEN", help="Reject by token")
    parser.add_argument("--list", action="store_true", help="List pending reviews")
    args = parser.parse_args()

    db = Database()
    await db.initialize()

    try:
        tracker = ApprovalTracker(db=db)

        if args.approve:
            result = await tracker.approve(args.approve)
            if result:
                print(f"  APPROVED: {args.approve}")
            else:
                print(f"  FAILED: Token not found or already processed")
            return

        if args.reject:
            result = await tracker.reject(args.reject, notes="Rejected via CLI")
            if result:
                print(f"  REJECTED: {args.reject}")
            else:
                print(f"  FAILED: Token not found or already processed")
            return

        # Interactive mode: list and select
        pending = await db.fetch_all(
            """
            SELECT r.id, r.brand_id, r.review_token, r.created_at,
                   s.hook, s.hook_type, v.duration_seconds
            FROM reviews r
            JOIN videos v ON v.id = r.video_id
            LEFT JOIN scripts s ON s.id = v.script_id
            WHERE r.status = 'pending'
            ORDER BY r.created_at ASC
            """
        )

        if not pending:
            print("\n  No pending reviews.")
            return

        print(f"\n  PENDING REVIEWS ({len(pending)})")
        print("=" * 70)
        for i, r in enumerate(pending, 1):
            hook = (r["hook"] or "—")[:40]
            brand = r["brand_id"].replace("_success_guru", "").title()
            duration = f"{r.get('duration_seconds', 0):.0f}s"
            print(
                f"  {i}. [{brand}] {hook} ({duration})"
            )
            print(f"     Token: {r['review_token']}")

        print("\n  Enter number to review, or 'q' to quit:")
        choice = input("  > ").strip()

        if choice.lower() == "q":
            return

        try:
            idx = int(choice) - 1
            review = pending[idx]
        except (ValueError, IndexError):
            print("  Invalid selection")
            return

        print(f"\n  Review: {review['hook']}")
        print(f"  Brand: {review['brand_id']}")
        print(f"  Token: {review['review_token']}")

        action = input("\n  (a)pprove / (r)eject / (s)kip: ").strip().lower()

        if action == "a":
            await tracker.approve(review["review_token"])
            print("  APPROVED")
        elif action == "r":
            notes = input("  Rejection reason (optional): ").strip()
            await tracker.reject(review["review_token"], notes=notes or None)
            print("  REJECTED")
        else:
            print("  Skipped")

    except Exception as exc:
        print(f"  ERROR: {exc}")
        sys.exit(1)
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
