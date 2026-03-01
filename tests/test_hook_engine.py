"""Tests for HookEngine — hook generation and weighted selection."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch


class TestHookEngine(unittest.TestCase):
    """Test HookEngine hook generation and type selection."""

    def test_import(self) -> None:
        """HookEngine should be importable."""
        from modules.ai_brain.hook_engine import HookEngine
        self.assertTrue(callable(HookEngine))

    def test_default_weight(self) -> None:
        """Default hook weight should be 1.0."""
        from modules.ai_brain.hook_engine import HookEngine
        self.assertEqual(HookEngine.DEFAULT_WEIGHT, 1.0)

    def test_min_weight(self) -> None:
        """Minimum hook weight should be 0.1."""
        from modules.ai_brain.hook_engine import HookEngine
        self.assertEqual(HookEngine.MIN_WEIGHT, 0.1)

    def test_hook_templates_populated(self) -> None:
        """HOOK_TEMPLATES should contain at least one category."""
        from modules.ai_brain.hook_engine import HookEngine
        self.assertIsInstance(HookEngine.HOOK_TEMPLATES, dict)
        self.assertGreater(len(HookEngine.HOOK_TEMPLATES), 0)

    def test_hook_template_values_are_lists(self) -> None:
        """Each hook template category should map to a list of strings."""
        from modules.ai_brain.hook_engine import HookEngine
        for category, templates in HookEngine.HOOK_TEMPLATES.items():
            self.assertIsInstance(
                templates, list,
                f"Category '{category}' templates not a list",
            )
            for tmpl in templates:
                self.assertIsInstance(
                    tmpl, str,
                    f"Template in '{category}' is not a string",
                )

    def test_has_generate_method(self) -> None:
        """HookEngine should have a generate method."""
        from modules.ai_brain.hook_engine import HookEngine
        engine = HookEngine()
        self.assertTrue(
            hasattr(engine, 'generate_hook') or hasattr(engine, 'generate'),
            "Missing generate_hook or generate method",
        )


if __name__ == "__main__":
    unittest.main()
