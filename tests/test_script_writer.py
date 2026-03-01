"""Tests for ScriptWriter — video script generation."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch


class TestScriptWriter(unittest.TestCase):
    """Test ScriptWriter configuration and structure."""

    def test_import(self) -> None:
        """ScriptWriter should be importable."""
        from modules.ai_brain.script_writer import ScriptWriter
        self.assertTrue(callable(ScriptWriter))

    def test_target_word_count(self) -> None:
        """Target word count should be around 120 for 30-60s video."""
        from modules.ai_brain.script_writer import ScriptWriter
        self.assertEqual(ScriptWriter.TARGET_WORD_COUNT, 120)

    def test_max_word_count(self) -> None:
        """Max word count should be 180."""
        from modules.ai_brain.script_writer import ScriptWriter
        self.assertEqual(ScriptWriter.MAX_WORD_COUNT, 180)

    def test_min_word_count(self) -> None:
        """Min word count should be 60."""
        from modules.ai_brain.script_writer import ScriptWriter
        self.assertEqual(ScriptWriter.MIN_WORD_COUNT, 60)

    def test_max_generation_attempts(self) -> None:
        """Max generation attempts should be 3."""
        from modules.ai_brain.script_writer import ScriptWriter
        self.assertEqual(ScriptWriter.MAX_GENERATION_ATTEMPTS, 3)

    def test_has_generate_script_method(self) -> None:
        """Should have a generate_script method."""
        from modules.ai_brain.script_writer import ScriptWriter
        writer = ScriptWriter()
        self.assertTrue(
            hasattr(writer, 'generate_script') or hasattr(writer, 'generate'),
            "Missing generate_script or generate method",
        )

    def test_uses_llm_router(self) -> None:
        """ScriptWriter should use LLMRouter for generation."""
        from modules.ai_brain.script_writer import ScriptWriter
        writer = ScriptWriter()
        self.assertTrue(
            hasattr(writer, 'llm') or hasattr(writer, 'llm_router'),
            "Missing llm/llm_router dependency",
        )

    def test_uses_dedup(self) -> None:
        """ScriptWriter should use CrossBrandDeduplicator."""
        from modules.ai_brain.script_writer import ScriptWriter
        writer = ScriptWriter()
        self.assertTrue(
            hasattr(writer, 'dedup') or hasattr(writer, 'deduplicator'),
            "Missing dedup/deduplicator dependency",
        )


if __name__ == "__main__":
    unittest.main()
