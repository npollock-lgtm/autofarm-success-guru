"""
News trend scanner for AutoFarm Zero — Success Guru Network v6.0.

Scans news sources for trending topics relevant to each brand's niche.
Uses Google News RSS feeds (no API key required) to discover recent
articles matching brand keywords.

Each brand has news_keywords defined in brands.json under
trend_sources.news_keywords. The scanner searches for these keywords
in news headlines and extracts trend-worthy topics.
"""

import json
import time
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote_plus

import requests
import structlog

from modules.trend_scanner.base_scanner import BaseScanner, TrendItem
from modules.infrastructure.retry_handler import retry_with_backoff

logger = structlog.get_logger(__name__)


class NewsScanner(BaseScanner):
    """
    Scans news sources for trending topics via Google News RSS.

    Uses Google News RSS feeds which don't require authentication.
    Searches for brand-relevant keywords in recent news headlines
    and extracts topics suitable for content generation.

    Attributes:
        BASE_URL: Google News RSS base URL.
        ARTICLES_PER_KEYWORD: Max articles to fetch per keyword.
        REQUEST_DELAY: Seconds between requests to avoid rate limiting.
    """

    BASE_URL: str = "https://news.google.com/rss/search"
    ARTICLES_PER_KEYWORD: int = 10
    REQUEST_DELAY: float = 3.0

    def __init__(self) -> None:
        """
        Initializes the News scanner with an HTTP session.

        Side effects:
            Creates an HTTP session with appropriate headers.
        """
        super().__init__()
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'AutoFarm/6.0 News Scanner',
            'Accept': 'application/rss+xml, application/xml, text/xml',
        })

    def scan(self, brand_id: str, brand_config: dict) -> list[TrendItem]:
        """
        Scans news sources for topics relevant to the brand.

        Parameters:
            brand_id: Brand identifier.
            brand_config: Full brand config from brands.json.

        Returns:
            List of TrendItem objects from news sources.

        Side effects:
            Makes HTTP requests to Google News RSS.
            Stores trends in the database.
        """
        trend_sources = brand_config.get('trend_sources', {})
        news_keywords = trend_sources.get('news_keywords', [])

        if not news_keywords:
            logger.info("no_news_keywords_configured",
                          brand_id=brand_id)
            return []

        all_trends = []
        brand_keywords = self._extract_all_keywords(brand_config)

        for keyword in news_keywords:
            try:
                keyword_trends = self._fetch_news(
                    keyword, brand_keywords, brand_id
                )
                all_trends.extend(keyword_trends)
                time.sleep(self.REQUEST_DELAY)

            except Exception as e:
                logger.error("news_scan_error",
                              brand_id=brand_id,
                              keyword=keyword,
                              error=str(e))

        # Deduplicate by topic
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

        logger.info("news_scan_complete",
                      brand_id=brand_id,
                      keywords_scanned=len(news_keywords),
                      trends_found=len(unique_trends),
                      trends_stored=stored)

        return top_trends

    @retry_with_backoff(max_retries=2, base_delay=5.0,
                        retry_on=(requests.RequestException,))
    def _fetch_news(self, keyword: str,
                     brand_keywords: list[str],
                     brand_id: str) -> list[TrendItem]:
        """
        Fetches news articles for a single keyword.

        Parameters:
            keyword: Search keyword.
            brand_keywords: All brand keywords for relevance scoring.
            brand_id: Brand identifier for logging.

        Returns:
            List of TrendItem objects from news results.

        Side effects:
            Makes HTTP request to Google News RSS.
        """
        encoded_keyword = quote_plus(keyword)
        url = f"{self.BASE_URL}?q={encoded_keyword}&hl=en-US&gl=US&ceid=US:en"

        response = self.session.get(url, timeout=15)
        response.raise_for_status()

        trends = []

        try:
            root = ET.fromstring(response.content)
            channel = root.find('channel')
            if channel is None:
                return []

            items = channel.findall('item')

            for item in items[:self.ARTICLES_PER_KEYWORD]:
                title = item.findtext('title', '')
                link = item.findtext('link', '')
                pub_date = item.findtext('pubDate', '')
                description = item.findtext('description', '')

                if not title:
                    continue

                # Clean title (Google News appends " - Source Name")
                clean_title = title
                if ' - ' in title:
                    parts = title.rsplit(' - ', 1)
                    clean_title = parts[0].strip()

                # Score relevance
                full_text = f"{clean_title} {description}"
                relevance = self._calculate_relevance(
                    full_text, brand_keywords
                )

                raw_data = json.dumps({
                    'keyword': keyword,
                    'title': title,
                    'clean_title': clean_title,
                    'link': link,
                    'pub_date': pub_date,
                    'description': description[:500],
                })

                trends.append(TrendItem(
                    topic=clean_title,
                    source='news',
                    source_detail=f"google_news:{keyword}",
                    raw_data=raw_data,
                    relevance_score=relevance,
                    url=link,
                ))

        except ET.ParseError as e:
            logger.warning("rss_parse_error",
                            keyword=keyword, error=str(e))

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
