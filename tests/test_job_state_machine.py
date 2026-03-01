"""Tests for JobStateMachine — state transitions and validation."""

import unittest
from unittest.mock import AsyncMock, MagicMock


class TestJobState(unittest.TestCase):
    """Test JobState enum values."""

    def test_import(self) -> None:
        """JobState and related items should be importable."""
        from modules.infrastructure.job_state_machine import JobState
        self.assertIsNotNone(JobState)

    def test_all_states_present(self) -> None:
        """All pipeline states should be defined."""
        from modules.infrastructure.job_state_machine import JobState
        expected = [
            "TREND_FOUND", "SCRIPT_DRAFT", "SCRIPT_SAFETY_CHECK",
            "TTS_QUEUED", "TTS_DONE", "VIDEO_ASSEMBLY", "VIDEO_ASSEMBLED",
            "QUALITY_CHECK", "QUALITY_PASSED", "REVIEW_PENDING",
            "REVIEW_APPROVED", "REVIEW_REJECTED", "SCHEDULED",
            "PUBLISHING", "PUBLISHED", "FAILED",
        ]
        for state_name in expected:
            self.assertTrue(
                hasattr(JobState, state_name),
                f"Missing state: {state_name}",
            )


class TestValidTransitions(unittest.TestCase):
    """Test valid state transition map."""

    def test_transitions_importable(self) -> None:
        """VALID_TRANSITIONS should be importable."""
        from modules.infrastructure.job_state_machine import VALID_TRANSITIONS
        self.assertIsInstance(VALID_TRANSITIONS, dict)

    def test_trend_found_transitions(self) -> None:
        """TREND_FOUND should transition to SCRIPT_DRAFT or FAILED."""
        from modules.infrastructure.job_state_machine import (
            JobState, VALID_TRANSITIONS,
        )
        targets = VALID_TRANSITIONS.get(JobState.TREND_FOUND, [])
        self.assertIn(JobState.SCRIPT_DRAFT, targets)
        self.assertIn(JobState.FAILED, targets)

    def test_review_pending_transitions(self) -> None:
        """REVIEW_PENDING should transition to APPROVED, REJECTED, or FAILED."""
        from modules.infrastructure.job_state_machine import (
            JobState, VALID_TRANSITIONS,
        )
        targets = VALID_TRANSITIONS.get(JobState.REVIEW_PENDING, [])
        self.assertIn(JobState.REVIEW_APPROVED, targets)
        self.assertIn(JobState.REVIEW_REJECTED, targets)

    def test_published_is_terminal(self) -> None:
        """PUBLISHED should have no outgoing transitions (or only FAILED)."""
        from modules.infrastructure.job_state_machine import (
            JobState, VALID_TRANSITIONS,
        )
        targets = VALID_TRANSITIONS.get(JobState.PUBLISHED, [])
        # Terminal state — either empty or only FAILED
        non_fail = [t for t in targets if t != JobState.FAILED]
        self.assertEqual(len(non_fail), 0)


class TestJobStateMachine(unittest.TestCase):
    """Test JobStateMachine transition enforcement."""

    def setUp(self) -> None:
        self.db = MagicMock()
        self.db.fetch_one = AsyncMock(return_value=None)
        self.db.execute = AsyncMock(return_value=1)

    def test_import(self) -> None:
        """JobStateMachine should be importable."""
        from modules.infrastructure.job_state_machine import JobStateMachine
        self.assertTrue(callable(JobStateMachine))

    def test_has_transition_method(self) -> None:
        """Should have a transition method."""
        from modules.infrastructure.job_state_machine import JobStateMachine
        sm = JobStateMachine(db=self.db)
        self.assertTrue(
            hasattr(sm, 'transition') or hasattr(sm, 'advance'),
            "Missing transition/advance method",
        )


if __name__ == "__main__":
    unittest.main()
