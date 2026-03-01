"""Tests for Publisher — publishing pipeline orchestrator."""

import unittest
from unittest.mock import AsyncMock, MagicMock


class TestPublisher(unittest.TestCase):
    """Test Publisher class configuration and methods."""

    def setUp(self) -> None:
        self.db = MagicMock()
        self.db.fetch_all = AsyncMock(return_value=[])
        self.db.fetch_one = AsyncMock(return_value=None)
        self.db.execute = AsyncMock(return_value=1)
        self.rate_limiter = MagicMock()
        self.scheduler = MagicMock()
        self.ip_router = MagicMock()
        self.credential_manager = MagicMock()

    def test_import(self) -> None:
        """Publisher should be importable."""
        from modules.publish_engine.publisher import Publisher
        self.assertTrue(callable(Publisher))

    def test_instantiation(self) -> None:
        """Publisher should accept all required dependencies."""
        from modules.publish_engine.publisher import Publisher
        pub = Publisher(
            db=self.db,
            rate_limiter=self.rate_limiter,
            scheduler=self.scheduler,
            ip_router=self.ip_router,
            credential_manager=self.credential_manager,
        )
        self.assertIsNotNone(pub)

    def test_max_retries(self) -> None:
        """MAX_RETRIES should be defined."""
        from modules.publish_engine.publisher import MAX_RETRIES
        self.assertGreater(MAX_RETRIES, 0)
        self.assertLessEqual(MAX_RETRIES, 10)

    def test_has_publish_method(self) -> None:
        """Should have a publish or publish_due method."""
        from modules.publish_engine.publisher import Publisher
        pub = Publisher(
            db=self.db,
            rate_limiter=self.rate_limiter,
            scheduler=self.scheduler,
            ip_router=self.ip_router,
            credential_manager=self.credential_manager,
        )
        self.assertTrue(
            hasattr(pub, 'publish_due') or hasattr(pub, 'publish'),
            "Missing publish_due/publish method",
        )

    def test_notifier_optional(self) -> None:
        """Publisher should work without a notifier."""
        from modules.publish_engine.publisher import Publisher
        pub = Publisher(
            db=self.db,
            rate_limiter=self.rate_limiter,
            scheduler=self.scheduler,
            ip_router=self.ip_router,
            credential_manager=self.credential_manager,
            notifier=None,
        )
        self.assertIsNone(pub.notifier)

    def test_notifier_stored(self) -> None:
        """Publisher should store notifier when provided."""
        from modules.publish_engine.publisher import Publisher
        notifier = MagicMock()
        pub = Publisher(
            db=self.db,
            rate_limiter=self.rate_limiter,
            scheduler=self.scheduler,
            ip_router=self.ip_router,
            credential_manager=self.credential_manager,
            notifier=notifier,
        )
        self.assertIs(pub.notifier, notifier)


class TestPlatformPublishers(unittest.TestCase):
    """Test individual platform publisher imports."""

    def test_tiktok_publisher(self) -> None:
        """TikTok publisher should be importable."""
        from modules.publish_engine.tiktok import TikTokPublisher
        self.assertTrue(callable(TikTokPublisher))

    def test_instagram_publisher(self) -> None:
        """Instagram publisher should be importable."""
        from modules.publish_engine.instagram import InstagramPublisher
        self.assertTrue(callable(InstagramPublisher))

    def test_facebook_publisher(self) -> None:
        """Facebook publisher should be importable."""
        from modules.publish_engine.facebook import FacebookPublisher
        self.assertTrue(callable(FacebookPublisher))

    def test_youtube_publisher(self) -> None:
        """YouTube publisher should be importable."""
        from modules.publish_engine.youtube import YouTubePublisher
        self.assertTrue(callable(YouTubePublisher))

    def test_snapchat_publisher(self) -> None:
        """Snapchat publisher should be importable."""
        from modules.publish_engine.snapchat import SnapchatPublisher
        self.assertTrue(callable(SnapchatPublisher))


if __name__ == "__main__":
    unittest.main()
