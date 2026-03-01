"""
Base trend scanner for AutoFarm Zero — Success Guru Network v6.0.

Abstract base class for all trend scanners. Defines the interface
that specific scanners (Reddit, Google Trends, News) must implement.
Provides common functionality for trend storage, deduplication,
and relevance scoring.
"""

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog

from database.db import Database

logger = structlog.get_logger(__name__)


class TrendItem:
    """
    Represents a discovered trend item.

    Attributes:
        topic: The trend topic/title.
        source: Source identifier (e.g., 'reddit', 'google_trends').
        source_detail: Specific source (e.g., subreddit name).
        raw_data: Original data from the source.
        relevance_score: 0.0-1.0 relevance to the brand niche.
        url: Optional URL to the source content.
        engagement: Optional engagement metric (upvotes, search volume).
    """

    def __init__(self, topic: str, source: str,
                 source_detail: str = "",
                 raw_data: str = "",
                 relevance_score: float = 0.5,
                 url: str = "",
                 engagement: int = 0) -> None:
        """
        Initializes a TrendItem.

        Parameters:
            topic: The trend topic/title.
            source: Source identifier.
            source_detail: Specific source within the platform.
            raw_data: Raw data from the source (JSON string).
            relevance_score: Relevance to the brand (0.0-1.0).
            url: Optional URL to the source.
            engagement: Engagement metric (upvotes, views, etc.).
        """
        self.topic = topic
        self.source = source
        self.source_detail = source_detail
        self.raw_data = raw_data
        self.relevance_score = relevance_score
        self.url = url
        self.engagement = engagement
        self.discovered_at = datetime.now(timezone.utc)


class BaseScanner(ABC):
    """
    Abstract base class for trend scanners.

    Provides common methods for storing and managing discovered trends.
    Subclasses must implement the scan() method for their specific source.

    Attributes:
        TREND_EXPIRY_DAYS: How long trends remain relevant.
        MIN_RELEVANCE_SCORE: Minimum score to store a trend.
        MAX_TRENDS_PER_SCAN: Maximum trends to return per scan.
    """

    TREND_EXPIRY_DAYS: int = 7
    MIN_RELEVANCE_SCORE: float = 0.3
    MAX_TRENDS_PER_SCAN: int = 20

    def __init__(self) -> None:
        """
        Initializes the base scanner with database access.

        Side effects:
            Creates a Database instance.
        """
        self.db = Database()

    @abstractmethod
    def scan(self, brand_id: str, brand_config: dict) -> list[TrendItem]:
        """
        Scans for trends relevant to the given brand.

        Parameters:
            brand_id: Brand identifier.
            brand_config: Full brand configuration from brands.json.

        Returns:
            List of TrendItem objects discovered.

        Side effects:
            Makes external API calls to the trend source.
        """
        pass

    def store_trends(self, brand_id: str,
                     trends: list[TrendItem]) -> int:
        """
        Stores discovered trends in the database, deduplicating.

        Parameters:
            brand_id: Brand identifier.
            trends: List of TrendItem objects to store.

        Returns:
            Number of new (non-duplicate) trends stored.

        Side effects:
            Inserts rows into the trends table.
            Skips trends that match existing topics for this brand.
        """
        stored_count = 0

        for trend in trends:
            if trend.relevance_score < self.MIN_RELEVANCE_SCORE:
                continue

            # Check for duplicates
            existing = self.db.fetch_one(
                "SELECT id FROM trends WHERE brand_id=? AND topic=? "
                "AND discovered_at > ?",
                (brand_id, trend.topic,
                 (datetime.now(timezone.utc) - timedelta(
                     days=self.TREND_EXPIRY_DAYS)).isoformat())
            )

            if existing:
                continue

            expires_at = (
                datetime.now(timezone.utc) + timedelta(
                    days=self.TREND_EXPIRY_DAYS)
            ).isoformat()

            self.db.execute_write(
                "INSERT INTO trends "
                "(brand_id, source, topic, raw_data, relevance_score, "
                "discovered_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (brand_id, trend.source, trend.topic,
                 trend.raw_data, trend.relevance_score,
                 trend.discovered_at.isoformat(), expires_at)
            )
            stored_count += 1

        if stored_count > 0:
            logger.info("trends_stored",
                          brand_id=brand_id,
                          source=self.__class__.__name__,
                          stored=stored_count,
                          total_scanned=len(trends))

        return stored_count

    def get_unused_trends(self, brand_id: str,
                          limit: int = 5) -> list[dict]:
        """
        Gets unused trends for content generation, ordered by relevance.

        Parameters:
            brand_id: Brand identifier.
            limit: Maximum number of trends to return.

        Returns:
            List of trend dicts from the database.

        Side effects:
            Reads from the trends table.
        """
        rows = self.db.fetch_all(
            "SELECT * FROM trends "
            "WHERE brand_id=? AND used=0 "
            "AND (expires_at IS NULL OR expires_at > ?) "
            "ORDER BY relevance_score DESC "
            "LIMIT ?",
            (brand_id, datetime.now(timezone.utc).isoformat(), limit)
        )
        return [dict(row) for row in rows]

    def mark_trend_used(self, trend_id: int) -> None:
        """
        Marks a trend as used (consumed for content generation).

        Parameters:
            trend_id: ID of the trend to mark.

        Side effects:
            Updates the used flag in the trends table.
        """
        self.db.execute_write(
            "UPDATE trends SET used=1 WHERE id=?",
            (trend_id,)
        )

    def cleanup_expired(self) -> int:
        """
        Removes expired trends from the database.

        Returns:
            Number of expired trends deleted.

        Side effects:
            Deletes rows from the trends table.
        """
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute_write(
            "DELETE FROM trends WHERE expires_at < ?",
            (now,)
        )
        # Can't easily get count from execute_write, just log
        logger.info("expired_trends_cleaned")
        return 0

    def _calculate_relevance(self, text: str,
                              keywords: list[str]) -> float:
        """
        Calculates keyword-based relevance score for a trend text.

        Parameters:
            text: The trend text to score.
            keywords: List of relevant keywords for the brand.

        Returns:
            Relevance score between 0.0 and 1.0.
        """
        if not text or not keywords:
            return 0.0

        text_lower = text.lower()
        matches = sum(
            1 for kw in keywords
            if kw.lower() in text_lower
        )

        # Normalize: at least 1 keyword match = 0.5, all = 1.0
        if matches == 0:
            return 0.2  # Base relevance for being in the right subreddit/category
        return min(0.5 + (matches / len(keywords)) * 0.5, 1.0)
