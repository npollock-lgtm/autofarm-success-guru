"""
Google Trends scanner for AutoFarm Zero — Success Guru Network v6.0.

Scans Google Trends for trending searches related to each brand's
niche keywords. Uses the pytrends library (unofficial Google Trends API)
to discover rising and breakout search terms.

Each brand has google_trends_keywords defined in brands.json under
trend_sources.google_trends_keywords. The scanner queries related
topics and rising queries for these seed keywords.
"""

import json
import time
import logging
from datetime import datetime, timezone
from typing import Optional

import structlog

from modules.trend_scanner.base_scanner import BaseScanner, TrendItem
from modules.infrastructure.retry_handler import retry_with_backoff

logger = structlog.get_logger(__name__)


class GoogleTrendsScanner(BaseScanner):
    """
    Scans Google Trends for trending search topics.

    Uses pytrends library to query Google Trends for topics related
    to each brand's configured keywords. Focuses on rising and
    breakout trends that indicate emerging content opportunities.

    Attributes:
        TIMEFRAME: Google Trends timeframe for queries.
        GEO: Geographic region for trends.
        MAX_KEYWORDS_PER_REQUEST: Max keywords per pytrends request (5).
        REQUEST_DELAY: Seconds between requests to avoid blocking.
    """

    TIMEFRAME: str = 'now 7-d'  # Last 7 days
    GEO: str = ''  # Worldwide
    MAX_KEYWORDS_PER_REQUEST: int = 5
    REQUEST_DELAY: float = 5.0  # Google can be aggressive with rate limits

    def __init__(self) -> None:
        """
        Initializes the Google Trends scanner.

        Side effects:
            Attempts to import pytrends. Sets availability flag.
        """
        super().__init__()
        self._pytrends_available = False
        try:
            from pytrends.request import TrendReq
            self._TrendReq = TrendReq
            self._pytrends_available = True
        except ImportError:
            logger.warning("pytrends_not_installed",
                            msg="pip install pytrends to enable Google Trends scanning")

    def scan(self, brand_id: str, brand_config: dict) -> list[TrendItem]:
        """
        Scans Google Trends for topics relevant to the brand.

        Parameters:
            brand_id: Brand identifier.
            brand_config: Full brand config from brands.json.

        Returns:
            List of TrendItem objects from Google Trends.

        Side effects:
            Makes HTTP requests to Google Trends API.
            Stores trends in the database.
        """
        if not self._pytrends_available:
            logger.info("google_trends_skipped",
                          brand_id=brand_id,
                          reason="pytrends not installed")
            return []

        trend_sources = brand_config.get('trend_sources', {})
        gt_keywords = trend_sources.get('google_trends_keywords', [])

        if not gt_keywords:
            logger.info("no_google_trends_keywords",
                          brand_id=brand_id)
            return []

        all_trends = []
        brand_keywords = self._extract_all_keywords(brand_config)

        # Process keywords in batches of 5 (Google Trends limit)
        for i in range(0, len(gt_keywords), self.MAX_KEYWORDS_PER_REQUEST):
            batch = gt_keywords[i:i + self.MAX_KEYWORDS_PER_REQUEST]

            try:
                batch_trends = self._fetch_trends_batch(
                    batch, brand_keywords, brand_id
                )
                all_trends.extend(batch_trends)

                # Delay between batches
                time.sleep(self.REQUEST_DELAY)

            except Exception as e:
                logger.error("google_trends_batch_error",
                              brand_id=brand_id,
                              keywords=batch,
                              error=str(e))

        # Deduplicate
        seen = set()
        unique_trends = []
        for trend in all_trends:
            key = trend.topic.lower().strip()
            if key not in seen:
                seen.add(key)
                unique_trends.append(trend)

        # Sort and limit
        unique_trends.sort(key=lambda t: t.relevance_score, reverse=True)
        top_trends = unique_trends[:self.MAX_TRENDS_PER_SCAN]

        # Store
        stored = self.store_trends(brand_id, top_trends)

        logger.info("google_trends_scan_complete",
                      brand_id=brand_id,
                      keywords_queried=len(gt_keywords),
                      trends_found=len(unique_trends),
                      trends_stored=stored)

        return top_trends

    @retry_with_backoff(max_retries=2, base_delay=10.0,
                        retry_on=(Exception,))
    def _fetch_trends_batch(self, keywords: list[str],
                             brand_keywords: list[str],
                             brand_id: str) -> list[TrendItem]:
        """
        Fetches trends for a batch of keywords (max 5).

        Parameters:
            keywords: List of seed keywords to query.
            brand_keywords: All brand keywords for relevance scoring.
            brand_id: Brand identifier for logging.

        Returns:
            List of TrendItem objects from this batch.

        Side effects:
            Makes HTTP requests to Google Trends.
        """
        pytrends = self._TrendReq(
            hl='en-US',
            tz=0,
            timeout=(10, 30),
            retries=2,
            backoff_factor=0.5,
        )

        pytrends.build_payload(
            keywords,
            timeframe=self.TIMEFRAME,
            geo=self.GEO,
        )

        trends = []

        # Get related queries
        try:
            related = pytrends.related_queries()
            for keyword, data in related.items():
                if data is None:
                    continue

                # Rising queries — most valuable
                rising = data.get('rising')
                if rising is not None and not rising.empty:
                    for _, row in rising.iterrows():
                        query_text = row.get('query', '')
                        value = row.get('value', 0)

                        if not query_text:
                            continue

                        relevance = self._calculate_relevance(
                            query_text, brand_keywords
                        )

                        # Boost breakout trends
                        if isinstance(value, str) and 'Breakout' in str(value):
                            relevance = min(relevance + 0.2, 1.0)
                            value_int = 10000
                        else:
                            try:
                                value_int = int(value)
                            except (ValueError, TypeError):
                                value_int = 0

                        raw_data = json.dumps({
                            'keyword': keyword,
                            'query': query_text,
                            'value': str(value),
                            'type': 'rising',
                        })

                        trends.append(TrendItem(
                            topic=query_text,
                            source='google_trends',
                            source_detail=f"rising:{keyword}",
                            raw_data=raw_data,
                            relevance_score=relevance,
                            engagement=value_int,
                        ))

                # Top queries
                top = data.get('top')
                if top is not None and not top.empty:
                    for _, row in top.head(10).iterrows():
                        query_text = row.get('query', '')
                        value = row.get('value', 0)

                        if not query_text:
                            continue

                        relevance = self._calculate_relevance(
                            query_text, brand_keywords
                        )

                        raw_data = json.dumps({
                            'keyword': keyword,
                            'query': query_text,
                            'value': str(value),
                            'type': 'top',
                        })

                        trends.append(TrendItem(
                            topic=query_text,
                            source='google_trends',
                            source_detail=f"top:{keyword}",
                            raw_data=raw_data,
                            relevance_score=relevance,
                            engagement=int(value) if value else 0,
                        ))

        except Exception as e:
            logger.warning("related_queries_failed",
                            keywords=keywords, error=str(e))

        # Get related topics
        try:
            topics = pytrends.related_topics()
            for keyword, data in topics.items():
                if data is None:
                    continue

                rising = data.get('rising')
                if rising is not None and not rising.empty:
                    for _, row in rising.iterrows():
                        topic_title = row.get('topic_title', '')
                        value = row.get('value', 0)

                        if not topic_title:
                            continue

                        relevance = self._calculate_relevance(
                            topic_title, brand_keywords
                        )

                        raw_data = json.dumps({
                            'keyword': keyword,
                            'topic': topic_title,
                            'topic_type': row.get('topic_type', ''),
                            'value': str(value),
                            'type': 'rising_topic',
                        })

                        trends.append(TrendItem(
                            topic=topic_title,
                            source='google_trends',
                            source_detail=f"topic:{keyword}",
                            raw_data=raw_data,
                            relevance_score=relevance,
                            engagement=int(value) if value else 0,
                        ))

        except Exception as e:
            logger.warning("related_topics_failed",
                            keywords=keywords, error=str(e))

        return trends

    def _extract_all_keywords(self, brand_config: dict) -> list[str]:
        """
        Extracts all keywords from brand config for relevance scoring.

        Parameters:
            brand_config: Full brand configuration.

        Returns:
            Deduplicated list of lowercase keywords.
        """
        keywords = []

        keywords.extend(brand_config.get('pillars', []))
        niche = brand_config.get('niche', '')
        if niche:
            keywords.extend(niche.lower().split())

        trend_sources = brand_config.get('trend_sources', {})
        keywords.extend(trend_sources.get('news_keywords', []))
        keywords.extend(trend_sources.get('google_trends_keywords', []))

        seen = set()
        clean = []
        for kw in keywords:
            kw_lower = kw.lower().strip()
            if kw_lower and kw_lower not in seen and len(kw_lower) > 2:
                seen.add(kw_lower)
                clean.append(kw_lower)

        return clean
