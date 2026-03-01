"""
Approval Tracker — manages review tokens and their lifecycle.

Tokens flow through: created → pending → approved | rejected | expired.

Auto-approval windows are enforced by the ``check_auto_approvals`` cron job
(runs every 30 minutes).  When a token expires and the brand is configured
for auto-approve, the video is automatically moved to the publish queue.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("autofarm.review_gate.approval_tracker")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TOKEN_LENGTH = 32  # Characters in generated token
DEFAULT_AUTO_APPROVE_HOURS = 24  # Auto-approve after this if no response


# ---------------------------------------------------------------------------
# ApprovalTracker
# ---------------------------------------------------------------------------


class ApprovalTracker:
    """Track approval tokens from creation through decision or expiry.

    Parameters
    ----------
    db:
        Database helper instance.
    auto_approve_hours:
        Default hours before auto-approval (if enabled per brand).
    """

    def __init__(
        self,
        db: Any,
        auto_approve_hours: float = DEFAULT_AUTO_APPROVE_HOURS,
    ) -> None:
        self.db = db
        self.auto_approve_hours = auto_approve_hours

    # ------------------------------------------------------------------
    # Token creation
    # ------------------------------------------------------------------

    async def create_token(
        self,
        review_id: int,
        brand_id: str = "",
        auto_approve: bool = False,
    ) -> str:
        """Generate a unique approval token for a review.

        Parameters
        ----------
        review_id:
            Primary key in the ``reviews`` table.
        brand_id:
            Brand identifier (for context / logging).
        auto_approve:
            Whether this review should auto-approve on expiry.

        Returns
        -------
        str
            The generated token string.

        Side Effects
        ------------
        Inserts a row into ``approval_tokens``.
        """
        token = secrets.token_urlsafe(TOKEN_LENGTH)
        expires_at = (
            datetime.now(timezone.utc) + timedelta(hours=self.auto_approve_hours)
        ).isoformat()

        await self.db.execute(
            """
            INSERT INTO approval_tokens
                (token, review_id, brand_id, status, auto_approve, expires_at, created_at)
            VALUES (?, ?, ?, 'pending', ?, ?, ?)
            """,
            (
                token,
                review_id,
                brand_id,
                1 if auto_approve else 0,
                expires_at,
                datetime.now(timezone.utc).isoformat(),
            ),
        )

        logger.info(
            "Created approval token for review %d (brand=%s, auto=%s)",
            review_id, brand_id, auto_approve,
        )
        return token

    # ------------------------------------------------------------------
    # Token status
    # ------------------------------------------------------------------

    async def get_token_status(self, token: str) -> Optional[Dict[str, Any]]:
        """Retrieve the full status of an approval token.

        Parameters
        ----------
        token:
            The approval token string.

        Returns
        -------
        Optional[Dict[str, Any]]
            Token record as dict, or ``None`` if not found.
        """
        row = await self.db.fetch_one(
            """
            SELECT token, review_id, brand_id, status, auto_approve,
                   expires_at, decided_at, created_at
            FROM approval_tokens
            WHERE token = ?
            """,
            (token,),
        )
        return dict(row) if row else None

    async def validate_token(self, token: str) -> bool:
        """Check whether a token is valid and still pending.

        Parameters
        ----------
        token:
            The approval token.

        Returns
        -------
        bool
            ``True`` if token exists and status is ``'pending'``.
        """
        status = await self.get_token_status(token)
        if status is None:
            return False
        return status["status"] == "pending"

    # ------------------------------------------------------------------
    # Decisions
    # ------------------------------------------------------------------

    async def mark_approved(self, token: str) -> bool:
        """Mark a token as approved.

        Parameters
        ----------
        token:
            The approval token.

        Returns
        -------
        bool
            ``True`` if the token was pending and is now approved.

        Side Effects
        ------------
        Updates the token status and decision timestamp.
        """
        if not await self.validate_token(token):
            logger.warning("Cannot approve invalid/expired token: %s", token[:8])
            return False

        now = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            """
            UPDATE approval_tokens
            SET status = 'approved', decided_at = ?
            WHERE token = ?
            """,
            (now, token),
        )
        logger.info("Token approved: %s", token[:8])
        return True

    async def mark_rejected(self, token: str, reason: str = "") -> bool:
        """Mark a token as rejected.

        Parameters
        ----------
        token:
            The approval token.
        reason:
            Optional rejection reason.

        Returns
        -------
        bool
            ``True`` if the token was pending and is now rejected.

        Side Effects
        ------------
        Updates token status.
        """
        if not await self.validate_token(token):
            logger.warning("Cannot reject invalid/expired token: %s", token[:8])
            return False

        now = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            """
            UPDATE approval_tokens
            SET status = 'rejected', decided_at = ?
            WHERE token = ?
            """,
            (now, token),
        )
        logger.info("Token rejected: %s (reason: %s)", token[:8], reason)
        return True

    # ------------------------------------------------------------------
    # Auto-approval expiry check
    # ------------------------------------------------------------------

    async def check_expiry(self) -> List[Dict[str, Any]]:
        """Check for expired tokens and auto-approve where configured.

        Returns
        -------
        List[Dict[str, Any]]
            List of tokens that were auto-approved.

        Side Effects
        ------------
        Updates expired auto-approve tokens to ``'approved'``.
        """
        now = datetime.now(timezone.utc).isoformat()
        expired_rows = await self.db.fetch_all(
            """
            SELECT token, review_id, brand_id, auto_approve
            FROM approval_tokens
            WHERE status = 'pending' AND expires_at <= ?
            """,
            (now,),
        )

        auto_approved: List[Dict[str, Any]] = []
        for row in expired_rows:
            token = row["token"]
            if row["auto_approve"]:
                # Auto-approve
                await self.db.execute(
                    """
                    UPDATE approval_tokens
                    SET status = 'approved', decided_at = ?
                    WHERE token = ?
                    """,
                    (now, token),
                )
                auto_approved.append(dict(row))
                logger.info(
                    "Auto-approved expired token for review %d (brand=%s)",
                    row["review_id"], row["brand_id"],
                )
            else:
                # Mark as expired (no auto-approve)
                await self.db.execute(
                    """
                    UPDATE approval_tokens
                    SET status = 'expired', decided_at = ?
                    WHERE token = ?
                    """,
                    (now, token),
                )
                logger.info(
                    "Token expired without approval for review %d",
                    row["review_id"],
                )

        return auto_approved

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    async def get_pending_tokens(self) -> List[Dict[str, Any]]:
        """Return all pending approval tokens.

        Returns
        -------
        List[Dict[str, Any]]
        """
        rows = await self.db.fetch_all(
            """
            SELECT token, review_id, brand_id, auto_approve,
                   expires_at, created_at
            FROM approval_tokens
            WHERE status = 'pending'
            ORDER BY created_at ASC
            """
        )
        return [dict(r) for r in rows]

    async def get_review_id_for_token(self, token: str) -> Optional[int]:
        """Return the review_id associated with a token.

        Parameters
        ----------
        token:
            The approval token.

        Returns
        -------
        Optional[int]
            The review_id, or ``None`` if token not found.
        """
        row = await self.db.fetch_one(
            "SELECT review_id FROM approval_tokens WHERE token = ?",
            (token,),
        )
        return row["review_id"] if row else None
