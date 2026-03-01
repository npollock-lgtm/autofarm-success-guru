"""Tests for CrossBrandDeduplicator — cosine similarity rejection."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch


class TestCrossBrandDeduplicator(unittest.TestCase):
    """Test cross-brand content deduplication logic."""

    def test_import(self) -> None:
        """CrossBrandDeduplicator should be importable."""
        from modules.compliance.cross_brand_dedup import CrossBrandDeduplicator
        self.assertTrue(callable(CrossBrandDeduplicator))

    def test_default_threshold(self) -> None:
        """Default similarity threshold should be 0.7."""
        from modules.compliance.cross_brand_dedup import CrossBrandDeduplicator
        self.assertEqual(CrossBrandDeduplicator.SIMILARITY_THRESHOLD, 0.7)

    def test_custom_threshold(self) -> None:
        """Should accept a custom similarity threshold."""
        from modules.compliance.cross_brand_dedup import CrossBrandDeduplicator
        dedup = CrossBrandDeduplicator(similarity_threshold=0.5)
        self.assertEqual(dedup.SIMILARITY_THRESHOLD, 0.5)

    def test_window_size_configured(self) -> None:
        """Window size should be set for rolling comparison."""
        from modules.compliance.cross_brand_dedup import CrossBrandDeduplicator
        self.assertGreater(CrossBrandDeduplicator.WINDOW_SIZE, 0)

    def test_has_check_method(self) -> None:
        """Should have check_script_uniqueness method."""
        from modules.compliance.cross_brand_dedup import CrossBrandDeduplicator
        dedup = CrossBrandDeduplicator()
        self.assertTrue(hasattr(dedup, 'check_script_uniqueness'))
        self.assertTrue(callable(dedup.check_script_uniqueness))


if __name__ == "__main__":
    unittest.main()
