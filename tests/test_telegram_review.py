"""Tests for TelegramReviewer — Telegram Bot review channel."""

import os
import unittest
from unittest.mock import patch, MagicMock


class TestTelegramReviewer(unittest.TestCase):
    """Test TelegramReviewer configuration and methods."""

    def test_import(self) -> None:
        """TelegramReviewer should be importable."""
        from modules.review_gate.telegram_reviewer import TelegramReviewer
        self.assertTrue(callable(TelegramReviewer))

    def test_instantiation_with_params(self) -> None:
        """Should accept bot_token and chat_id."""
        from modules.review_gate.telegram_reviewer import TelegramReviewer
        reviewer = TelegramReviewer(
            bot_token="test-token-123",
            chat_id="test-chat-456",
        )
        self.assertEqual(reviewer.bot_token, "test-token-123")
        self.assertEqual(reviewer.chat_id, "test-chat-456")

    @patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "env-token",
        "TELEGRAM_REVIEW_CHAT_ID": "env-chat",
    })
    def test_falls_back_to_env_vars(self) -> None:
        """Should fall back to environment variables when params not given."""
        from modules.review_gate.telegram_reviewer import TelegramReviewer
        reviewer = TelegramReviewer()
        self.assertEqual(reviewer.bot_token, "env-token")
        self.assertEqual(reviewer.chat_id, "env-chat")

    def test_approval_base_url_default(self) -> None:
        """Approval base URL should have a default."""
        from modules.review_gate.telegram_reviewer import TelegramReviewer
        reviewer = TelegramReviewer(bot_token="t", chat_id="c")
        self.assertIn("8080", reviewer.approval_base_url)

    def test_approval_base_url_override(self) -> None:
        """Should accept a custom approval_base_url."""
        from modules.review_gate.telegram_reviewer import TelegramReviewer
        reviewer = TelegramReviewer(
            bot_token="t",
            chat_id="c",
            approval_base_url="http://custom:9090",
        )
        self.assertEqual(reviewer.approval_base_url, "http://custom:9090")

    def test_has_send_review_method(self) -> None:
        """Should have a method to send review packages."""
        from modules.review_gate.telegram_reviewer import TelegramReviewer
        reviewer = TelegramReviewer(bot_token="t", chat_id="c")
        self.assertTrue(
            hasattr(reviewer, 'send_review') or hasattr(reviewer, 'send_review_package'),
            "Missing send_review/send_review_package method",
        )


if __name__ == "__main__":
    unittest.main()
