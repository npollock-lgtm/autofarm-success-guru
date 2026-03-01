"""Tests for VideoAssembler — FFmpeg video composition."""

import unittest
from unittest.mock import MagicMock, patch


class TestVideoAssemblerConstants(unittest.TestCase):
    """Test VideoAssembler module-level constants."""

    def test_import(self) -> None:
        """VideoAssembler module should be importable."""
        from modules.content_forge import video_assembler
        self.assertIsNotNone(video_assembler)

    def test_output_dimensions(self) -> None:
        """Output should be 1080x1920 portrait."""
        from modules.content_forge.video_assembler import OUTPUT_WIDTH, OUTPUT_HEIGHT
        self.assertEqual(OUTPUT_WIDTH, 1080)
        self.assertEqual(OUTPUT_HEIGHT, 1920)

    def test_output_fps(self) -> None:
        """Output FPS should be 30."""
        from modules.content_forge.video_assembler import OUTPUT_FPS
        self.assertEqual(OUTPUT_FPS, 30)

    def test_crf_quality(self) -> None:
        """CRF quality setting should be reasonable (18-28)."""
        from modules.content_forge.video_assembler import CRF
        self.assertGreaterEqual(CRF, 18)
        self.assertLessEqual(CRF, 28)

    def test_platform_presets(self) -> None:
        """Platform presets should include TikTok."""
        from modules.content_forge.video_assembler import PLATFORM_PRESETS
        self.assertIn("tiktok", PLATFORM_PRESETS)


class TestVideoAssembler(unittest.TestCase):
    """Test VideoAssembler class."""

    def test_class_importable(self) -> None:
        """VideoAssembler class should be importable."""
        from modules.content_forge.video_assembler import VideoAssembler
        self.assertTrue(callable(VideoAssembler))

    def test_has_assemble_method(self) -> None:
        """Should have an assemble or assemble_video method."""
        from modules.content_forge.video_assembler import VideoAssembler
        assembler = VideoAssembler.__new__(VideoAssembler)
        self.assertTrue(
            hasattr(assembler, 'assemble') or hasattr(assembler, 'assemble_video'),
            "Missing assemble/assemble_video method",
        )


if __name__ == "__main__":
    unittest.main()
