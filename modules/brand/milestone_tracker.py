"""
Milestone Tracker — monitors follower / subscriber milestones and provides
strategic growth suggestions.

Detects when a brand reaches significant follower thresholds (1K, 5K, 10K,
25K, 50K, 100K, 250K, 500K, 1M, …) and:
  * Records the milestone in the ``milestones`` table.
  * Sends Telegram notifications.
  * Recommends content strategy adjustments for the new growth stage.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("autofarm.brand.milestone_tracker")

# ---------------------------------------------------------------------------
# Milestone definitions
# ---------------------------------------------------------------------------

MILESTONES: List[int] = [
    1_000,
    5_000,
    10_000,
    25_000,
    50_000,
    100_000,
    250_000,
    500_000,
    1_000_000,
    5_000_000,
    10_000_000,
]

MILESTONE_LABELS: Dict[int, str] = {
    1_000: "1k_followers",
    5_000: "5k_followers",
    10_000: "10k_followers",
    25_000: "25k_followers",
    50_000: "50k_followers",
    100_000: "100k_followers",
    250_000: "250k_followers",
    500_000: "500k_followers",
    1_000_000: "1m_followers",
    5_000_000: "5m_followers",
    10_000_000: "10m_followers",
}

# Strategy suggestions per milestone tier
STRATEGY_SUGGESTIONS: Dict[str, str] = {
    "1k_followers": (
        "Focus on engagement: reply to every comment, do Q&A content, "
        "and experiment with different posting times."
    ),
    "5k_followers": (
        "Start collaborating with micro-influencers in your niche. "
        "Consider consistent posting schedules to build habit."
    ),
    "10k_followers": (
        "Leverage platform features unlocked at 10K (e.g. swipe-up links). "
        "Begin A/B testing thumbnails and hooks systematically."
    ),
    "25k_followers": (
        "Diversify content formats — add series, behind-the-scenes, or live streams. "
        "Build an email list to own your audience."
    ),
    "50k_followers": (
        "Consider cross-platform expansion if not already present. "
        "Explore brand sponsorship opportunities for monetisation."
    ),
    "100k_followers": (
        "You're a macro-creator — invest in production quality. "
        "Develop a content team or SOPs to maintain consistency."
    ),
    "250k_followers": (
        "Build a community (Discord, subreddit, or Telegram group). "
        "Create premium / exclusive content tiers."
    ),
    "500k_followers": (
        "Consider launching your own product line or digital products. "
        "Optimise for platform algorithm features (longer videos, etc.)."
    ),
    "1m_followers": (
        "You are a top creator — focus on brand equity, media appearances, "
        "and long-term audience retention strategies."
    ),
    "5m_followers": (
        "Scale into a media company: multiple brands, managed channels, "
        "and licensing deals."
    ),
    "10m_followers": (
        "Iconic status — protect brand reputation, diversify revenue, "
        "and mentor rising creators."
    ),
}


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------


@dataclass
class MilestoneEvent:
    """Represents a milestone that was just reached."""

    account_id: int
    brand_id: str
    platform: str
    milestone_type: str
    follower_count: int
    suggestion: str = ""
    reached_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def to_dict(self) -> Dict[str, Any]:
        """Serialise for notification payload."""
        return {
            "account_id": self.account_id,
            "brand_id": self.brand_id,
            "platform": self.platform,
            "milestone_type": self.milestone_type,
            "follower_count": self.follower_count,
            "suggestion": self.suggestion,
            "reached_at": self.reached_at.isoformat(),
        }


# ---------------------------------------------------------------------------
# MilestoneTracker
# ---------------------------------------------------------------------------


class MilestoneTracker:
    """Track follower milestones per account and provide growth suggestions.

    Parameters
    ----------
    db:
        Database helper instance.
    notifier:
        Optional notification helper (e.g. Telegram bot) with an
        ``async send(message: str)`` method.
    """

    def __init__(self, db: Any, notifier: Optional[Any] = None) -> None:
        self.db = db
        self.notifier = notifier

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def check_milestones(
        self,
        account_id: int,
        brand_id: str,
        platform: str,
        current_followers: int,
    ) -> List[MilestoneEvent]:
        """Check whether any new milestones have been reached.

        Parameters
        ----------
        account_id:
            Primary key in the ``accounts`` table.
        brand_id:
            Brand identifier.
        platform:
            Platform name (tiktok, instagram, …).
        current_followers:
            Current follower / subscriber count.

        Returns
        -------
        List[MilestoneEvent]
            Newly reached milestones (may be empty).

        Side Effects
        ------------
        * Inserts rows into ``milestones`` for each new milestone.
        * Sends notifications via *notifier* if configured.
        """
        already_reached = await self._get_reached_milestones(account_id)
        new_events: List[MilestoneEvent] = []

        for threshold in MILESTONES:
            if current_followers < threshold:
                break  # sorted ascending — no need to check further
            label = MILESTONE_LABELS[threshold]
            if label in already_reached:
                continue  # already recorded

            suggestion = STRATEGY_SUGGESTIONS.get(label, "")
            event = MilestoneEvent(
                account_id=account_id,
                brand_id=brand_id,
                platform=platform,
                milestone_type=label,
                follower_count=current_followers,
                suggestion=suggestion,
            )
            await self._record_milestone(event)
            new_events.append(event)

            logger.info(
                "Milestone reached: %s on %s — %s (%d followers)",
                brand_id,
                platform,
                label,
                current_followers,
            )

        # Send notifications for new milestones
        if new_events and self.notifier:
            await self._notify(new_events)

        return new_events

    async def get_milestone_history(
        self, account_id: int
    ) -> List[Dict[str, Any]]:
        """Return all milestones for an account.

        Parameters
        ----------
        account_id:
            Account to query.

        Returns
        -------
        List[Dict[str, Any]]
        """
        rows = await self.db.fetch_all(
            """
            SELECT id, account_id, milestone_type, reached_at, notified
            FROM milestones
            WHERE account_id = ?
            ORDER BY reached_at ASC
            """,
            (account_id,),
        )
        return [dict(r) for r in rows]

    async def get_current_tier(
        self, account_id: int, current_followers: int
    ) -> Optional[str]:
        """Return the label of the highest milestone reached.

        Parameters
        ----------
        account_id:
            Account to query.
        current_followers:
            Current follower count.

        Returns
        -------
        Optional[str]
            Milestone label or ``None`` if below 1K.
        """
        highest: Optional[str] = None
        for threshold in MILESTONES:
            if current_followers >= threshold:
                highest = MILESTONE_LABELS[threshold]
            else:
                break
        return highest

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_reached_milestones(self, account_id: int) -> set:
        """Return the set of milestone_type strings already recorded.

        Parameters
        ----------
        account_id:
            Account to query.

        Returns
        -------
        set
            Set of milestone type labels.
        """
        rows = await self.db.fetch_all(
            "SELECT milestone_type FROM milestones WHERE account_id = ?",
            (account_id,),
        )
        return {row["milestone_type"] for row in rows}

    async def _record_milestone(self, event: MilestoneEvent) -> None:
        """Insert a milestone row.

        Parameters
        ----------
        event:
            Milestone event to record.

        Side Effects
        ------------
        Inserts one row into ``milestones``.
        """
        await self.db.execute(
            """
            INSERT INTO milestones (account_id, milestone_type, reached_at, notified)
            VALUES (?, ?, ?, 0)
            """,
            (
                event.account_id,
                event.milestone_type,
                event.reached_at.isoformat(),
            ),
        )

    async def _notify(self, events: List[MilestoneEvent]) -> None:
        """Send Telegram notifications for newly reached milestones.

        Parameters
        ----------
        events:
            List of milestone events.

        Side Effects
        ------------
        * Sends messages via ``self.notifier``.
        * Marks milestones as notified in the database.
        """
        for event in events:
            message = (
                f"🎉 Milestone Reached!\n"
                f"Brand: {event.brand_id}\n"
                f"Platform: {event.platform}\n"
                f"Milestone: {event.milestone_type}\n"
                f"Followers: {event.follower_count:,}\n"
            )
            if event.suggestion:
                message += f"\n💡 Strategy tip: {event.suggestion}"

            try:
                await self.notifier.send(message)
                # Mark as notified
                await self.db.execute(
                    """
                    UPDATE milestones SET notified = 1
                    WHERE account_id = ? AND milestone_type = ?
                    """,
                    (event.account_id, event.milestone_type),
                )
            except Exception as exc:
                logger.error(
                    "Failed to send milestone notification: %s", exc
                )
