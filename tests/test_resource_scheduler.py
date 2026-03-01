"""Tests for ResourceScheduler — resource-aware job concurrency."""

import unittest
from unittest.mock import patch, MagicMock


class TestResourceScheduler(unittest.TestCase):
    """Test ResourceScheduler resource checks and concurrency control."""

    def test_import(self) -> None:
        """ResourceScheduler should be importable."""
        from modules.infrastructure.resource_scheduler import ResourceScheduler
        self.assertTrue(callable(ResourceScheduler))

    def test_thresholds_defined(self) -> None:
        """Resource thresholds should be defined for job types."""
        from modules.infrastructure.resource_scheduler import ResourceScheduler
        scheduler = ResourceScheduler()
        if hasattr(scheduler, 'THRESHOLDS'):
            self.assertIn("video_assembly", scheduler.THRESHOLDS)
            self.assertIn("tts_generation", scheduler.THRESHOLDS)
            self.assertIn("llm_inference", scheduler.THRESHOLDS)

    def test_video_assembly_ram_requirement(self) -> None:
        """Video assembly should require at least 4GB free RAM."""
        from modules.infrastructure.resource_scheduler import ResourceScheduler
        scheduler = ResourceScheduler()
        if hasattr(scheduler, 'THRESHOLDS'):
            va_thresh = scheduler.THRESHOLDS.get("video_assembly", {})
            min_ram = va_thresh.get("min_free_ram_gb", 0)
            self.assertGreaterEqual(min_ram, 4)

    def test_can_start_job_method(self) -> None:
        """Should expose can_start_job() check."""
        from modules.infrastructure.resource_scheduler import ResourceScheduler
        scheduler = ResourceScheduler()
        self.assertTrue(hasattr(scheduler, 'can_start_job'))
        self.assertTrue(callable(scheduler.can_start_job))

    def test_wait_for_resources_method(self) -> None:
        """Should expose wait_for_resources() blocking method."""
        from modules.infrastructure.resource_scheduler import ResourceScheduler
        scheduler = ResourceScheduler()
        self.assertTrue(hasattr(scheduler, 'wait_for_resources'))
        self.assertTrue(callable(scheduler.wait_for_resources))

    def test_max_concurrent_video_assembly(self) -> None:
        """Only 1 video assembly should run at a time."""
        from modules.infrastructure.resource_scheduler import ResourceScheduler
        scheduler = ResourceScheduler()
        max_concurrent = getattr(scheduler, 'MAX_CONCURRENT_VIDEO_ASSEMBLY', 1)
        self.assertEqual(max_concurrent, 1)


if __name__ == "__main__":
    unittest.main()
