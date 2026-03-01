"""Tests for ReviewGate — review workflow orchestration."""

import unittest
from unittest.mock import AsyncMock, MagicMock


class TestReviewGate(unittest.TestCase):
    """Test ReviewGate orchestration of the review workflow."""

    def setUp(self) -> None:
        self.db = MagicMock()
        self.db.fetch_one = AsyncMock(return_value=None)
        self.db.fetch_all = AsyncMock(return_value=[])
        self.db.execute = AsyncMock(return_value=1)
        self.approval_tracker = MagicMock()
        self.telegram_reviewer = MagicMock()
        self.email_sender = MagicMock()
        self.content_queue = MagicMock()

    def test_import(self) -> None:
        """ReviewGate should be importable."""
        from modules.review_gate.gate import ReviewGate
        self.assertTrue(callable(ReviewGate))

    def test_instantiation(self) -> None:
        """ReviewGate should accept db and reviewer dependencies."""
        from modules.review_gate.gate import ReviewGate
        gate = ReviewGate(
            db=self.db,
            approval_tracker=self.approval_tracker,
            telegram_reviewer=self.telegram_reviewer,
            email_sender=self.email_sender,
            content_queue=self.content_queue,
        )
        self.assertIsNotNone(gate)

    def test_has_process_method(self) -> None:
        """Should have a process or submit_for_review method."""
        from modules.review_gate.gate import ReviewGate
        gate = ReviewGate(
            db=self.db,
            approval_tracker=self.approval_tracker,
            telegram_reviewer=self.telegram_reviewer,
            email_sender=self.email_sender,
            content_queue=self.content_queue,
        )
        self.assertTrue(
            hasattr(gate, 'process') or hasattr(gate, 'submit_for_review'),
            "Missing process/submit_for_review method",
        )

    def test_telegram_is_primary(self) -> None:
        """ReviewGate should use telegram_reviewer as primary channel."""
        from modules.review_gate.gate import ReviewGate
        gate = ReviewGate(
            db=self.db,
            approval_tracker=self.approval_tracker,
            telegram_reviewer=self.telegram_reviewer,
            email_sender=self.email_sender,
            content_queue=self.content_queue,
        )
        self.assertIs(gate.telegram_reviewer, self.telegram_reviewer)

    def test_email_is_fallback(self) -> None:
        """ReviewGate should hold email_sender as fallback."""
        from modules.review_gate.gate import ReviewGate
        gate = ReviewGate(
            db=self.db,
            approval_tracker=self.approval_tracker,
            telegram_reviewer=self.telegram_reviewer,
            email_sender=self.email_sender,
            content_queue=self.content_queue,
        )
        self.assertIs(gate.email_sender, self.email_sender)


if __name__ == "__main__":
    unittest.main()
