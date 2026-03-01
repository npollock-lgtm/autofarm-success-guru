"""Tests for the SmartScheduler — posting time selection and variation."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock


class TestSmartScheduler(unittest.TestCase):
    """Test SmartScheduler posting window logic."""

    def setUp(self) -> None:
        self.db = MagicMock()
        self.db.fetch_all = AsyncMock(return_value=[])
        self.db.fetch_one = AsyncMock(return_value=None)
        self.db.execute = AsyncMock(return_value=1)

    def test_import(self) -> None:
        """SmartScheduler should be importable."""
        from modules.publish_engine.scheduler import SmartScheduler
        scheduler = SmartScheduler(db=self.db)
        self.assertIsNotNone(scheduler)

    def test_schedule_config_loaded(self) -> None:
        """Schedule config should have data for all brands."""
        from modules.publish_engine.schedule_config import POSTING_WINDOWS_UTC
        self.assertIn("human_success_guru", POSTING_WINDOWS_UTC)
        self.assertIn("wealth_success_guru", POSTING_WINDOWS_UTC)
        self.assertIn("zen_success_guru", POSTING_WINDOWS_UTC)

    def test_deterministic_offset(self) -> None:
        """Same inputs should produce same offset (md5-based)."""
        from modules.publish_engine.scheduler import SmartScheduler
        scheduler = SmartScheduler(db=self.db)
        # Two calls with the same brand/platform/date should give same offset
        if hasattr(scheduler, '_deterministic_offset'):
            off1 = scheduler._deterministic_offset("test_brand", "tiktok", "2026-01-01")
            off2 = scheduler._deterministic_offset("test_brand", "tiktok", "2026-01-01")
            self.assertEqual(off1, off2)


class TestScheduleConfig(unittest.TestCase):
    """Test schedule configuration structure."""

    def test_all_brands_have_windows(self) -> None:
        """Every brand should have posting windows defined."""
        from modules.publish_engine.schedule_config import POSTING_WINDOWS_UTC
        brands = [
            "human_success_guru", "wealth_success_guru", "zen_success_guru",
            "social_success_guru", "habits_success_guru", "relationships_success_guru",
        ]
        for brand in brands:
            self.assertIn(brand, POSTING_WINDOWS_UTC, f"{brand} missing from schedule")


if __name__ == "__main__":
    unittest.main()
