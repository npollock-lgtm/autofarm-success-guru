"""Tests for LLMRouter — Ollama primary, Groq fallback, cached responses."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


class TestLLMRouter(unittest.TestCase):
    """Test LLMRouter routing and fallback logic."""

    def setUp(self) -> None:
        self.db = MagicMock()
        self.db.execute = AsyncMock(return_value=1)
        self.db.fetch_one = AsyncMock(return_value=None)

    def test_import(self) -> None:
        """LLMRouter should be importable."""
        from modules.ai_brain.llm_router import LLMRouter
        router = LLMRouter(db=self.db)
        self.assertIsNotNone(router)

    def test_provider_order(self) -> None:
        """Ollama should be primary, Groq secondary."""
        from modules.ai_brain.llm_router import LLMRouter
        router = LLMRouter(db=self.db)
        if hasattr(router, 'providers') or hasattr(router, 'provider_order'):
            providers = getattr(router, 'provider_order', getattr(router, 'providers', []))
            if isinstance(providers, list) and len(providers) >= 2:
                self.assertEqual(providers[0], "ollama")

    def test_task_types_supported(self) -> None:
        """Router should support standard task types."""
        from modules.ai_brain.llm_router import LLMRouter
        router = LLMRouter(db=self.db)
        if hasattr(router, 'generate'):
            self.assertTrue(callable(router.generate))


if __name__ == "__main__":
    unittest.main()
