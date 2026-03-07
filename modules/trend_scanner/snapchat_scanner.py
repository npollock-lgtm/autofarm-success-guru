"""
Snapchat trend scanner for AutoFarm Zero — Success Guru Network v6.0.

Scans for Snapchat-relevant viral trends using two indirect data sources.
Snapchat has the most closed ecosystem of all platforms — no public API
for trending content, no Creative Center, and Spotlight/Discover data
is only accessible within the app.

1. **Primary:** Google Trends via pytrends — queries brand keywords
   contextualized for Snapchat (e.g. "motivation snapchat",
   "psychology spotlight") to discover emerging Snapchat interest.

2. **Fallback:** Google News RSS — searches for articles about Snapchat
   trends in each brand's niche.

Scores are multiplied by 0.9 (``INDIRECT_SOURCE_DISCOUNT``) because
both data sources are indirect — they measure interest *about* Snapchat
rather than what is trending *on* Snapchat.

Each brand has ``snapchat_keywords`` defined in ``brands.json`` under
``trend_sources``.
"""

import json
import os
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


class SnapchatScanner(BaseScanner):
    """
    Scans for Snapchat viral trend topics.

    Uses Google Trends (primary) and Google News RSS (fallback) with
    Snapchat-contextualized keywords to discover trending topics on
    Snapchat relevant to each brand's niche.

    Applies a confidence discount to scores since both sources
    are indirect measures of Snapchat trends.

    Attributes:
        PYTRENDS_DELAY: Seconds between pytrends requests.
        NEWS_DELAY: Seconds between Google News RSS requests.
        GOOGLE_NEWS_URL: Google News RSS endpoint.
        INDIRECT_SOURCE_DISCOUNT: Multiplier for indirect source scores.
    """

    PYTRENDS_DELAY: float = 5.0
    NEWS_DELAY: float = 3.0
    GOOGLE_NEWS_URL: str = "https://news.google.com/rss/search"
    INDIRECT_SOURCE_DISCOUNT: float = 0.9

    def __init__(self) -> None:
        """
        Initializes the Snapchat scanner.

        Side effects:
            Attempts to import pytrends.
            Creates HTTP session for Google News RSS.
        """
        super().__init__()
        self._pytrends_available = False
        try:
            from pytrends.request import TrendReq
            self._TrendReq = TrendReq
            self._pytrends_available = True
        except ImportError:
            logger.info("pytrends_not_available_for_snapchat")

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "AutoFarm/6.0 Snapchat Scanner",
            "Accept": "application/rss+xml, application/xml, text/xml",
        })

    def scan(self, brand_id: str, brand_config: dict) -> list[TrendItem]:
        """
        Scans for Snapchat trends relevant to the brand.

        Parameters:
            brand_id: Brand identifier.
            brand_config: Full brand config from brands.json.

        Returns:
            List of TrendItem objects from Snapchat-related sources.

        Side effects:
            Makes HTTP requests to Google Trends and/or Google News RSS.
        """
        trend_sources = brand_config.get("trend_sources", {})
        snap_keywords = trend_sources.get("snapchat_keywords", [])

        if not snap_keywords:
            logger.info("no_snapchat_keywords", brand_id=brand_id)
            return []

        all_trends = []
        brand_keywords = self._extract_all_keywords(brand_config)

        # Primary: Google Trends with Snapchat context
        if self._pytrends_available:
            gt_trends = self._fetch_snapchat_google_trends(
                snap_keywords, brand_keywords, brand_id
            )
            all_trends.extend(gt_trends)

        # Fallback / supplement: Google News RSS
        news_trends = self._fetch_snapchat_news_trends(
            snap_keywords, brand_keywords, brand_id
        )
        all_trends.extend(news_trends)

        # Apply indirect source discount to all scores
        for trend in all_trends:
            trend.relevance_score = min(
                trend.relevance_score * self.INDIRECT_SOURCE_DISCOUNT, 1.0
            )

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
        top_trends = unique_trends[: self.MAX_TRENDS_PER_SCAN]

        # Store
        stored = self.store_trends(brand_id, top_trends)

        logger.info(
            "snapchat_scan_complete",
            brand_id=brand_id,
            trends_found=len(unique_trends),
            trends_stored=stored,
        )

        return top_trends

    # ------------------------------------------------------------------
    # Google Trends (primary)
    # ------------------------------------------------------------------

    def _fetch_snapchat_google_trends(
        self,
        keywords: list[str],
        brand_keywords: list[str],
        brand_id: str,
    ) -> list[TrendItem]:
        """
        Fetches Snapchat-contextualized search trends via pytrends.

        Parameters:
            keywords: Snapchat-specific seed keywords.
            brand_keywords: All brand keywords for relevance scoring.
            brand_id: Brand identifier for logging.

        Returns:
            List of TrendItem objects from Google Trends.

        Side effects:
            Makes HTTP requests to Google Trends.
        """
        trends = []
        max_per_request = 5

        for i in range(0, len(keywords), max_per_request):
            batch = keywords[i : i + max_per_request]

            try:
                batch_trends = self._fetch_trends_batch(
                    batch, brand_keywords, brand_id
                )
                trends.extend(batch_trends)
                time.sleep(self.PYTRENDS_DELAY)
            except Exception as e:
                logger.warning(
                    "snapchat_google_trends_batch_error",
                    brand_id=brand_id,
                    keywords=batch,
                    error=str(e),
                )

        return trends

    @retry_with_backoff(max_retries=2, base_delay=10.0, retry_on=(Exception,))
    def _fetch_trends_batch(
        self,
        keywords: list[str],
        brand_keywords: list[str],
        brand_id: str,
    ) -> list[TrendItem]:
        """
        Fetches a single batch of Snapchat search trends via pytrends.

        Parameters:
            keywords: Batch of seed keywords (max 5).
            brand_keywords: All brand keywords for relevance scoring.
            brand_id: Brand identifier for logging.

        Returns:
            List of TrendItem objects from this batch.

        Side effects:
            Makes HTTP requests to Google Trends.
        """
        # Patch urllib3 Retry for pytrends compatibility
        try:
            from urllib3.util.retry import Retry as _OrigRetry

            _orig_init = _OrigRetry.__init__

            def _patched_init(self_retry, *args, **kwargs):
                if "method_whitelist" in kwargs:
                    kwargs["allowed_methods"] = kwargs.pop("method_whitelist")
                return _orig_init(self_retry, *args, **kwargs)

            _OrigRetry.__init__ = _patched_init
        except Exception:
            pass

        pytrends = self._TrendReq(
            hl="en-US",
            tz=0,
            timeout=(10, 30),
            retries=2,
            backoff_factor=0.5,
        )

        pytrends.build_payload(
            keywords,
            timeframe="now 7-d",
            geo="",
        )

        trends = []

        # Related queries
        try:
            related = pytrends.related_queries()
            for keyword, data in related.items():
                if data is None:
                    continue

                # Rising queries
                rising = data.get("rising")
                if rising is not None and not rising.empty:
                    for _, row in rising.iterrows():
                        query_text = row.get("query", "")
                        value = row.get("value", 0)

                        if not query_text:
                            continue

                        relevance = self._calculate_relevance(
                            query_text, brand_keywords
                        )

                        # Boost breakout trends
                        if isinstance(value, str) and "Breakout" in str(value):
                            relevance = min(relevance + 0.2, 1.0)
                            value_int = 10000
                        else:
                            try:
                                value_int = int(value)
                            except (ValueError, TypeError):
                                value_int = 0

                        raw_data = json.dumps({
                            "keyword": keyword,
                            "query": query_text,
                            "value": str(value),
                            "type": "rising",
                            "platform_context": "snapchat",
                        })

                        trends.append(
                            TrendItem(
                                topic=query_text,
                                source="snapchat",
                                source_detail=f"google_trends:snapchat:{keyword}",
                                raw_data=raw_data,
                                relevance_score=relevance,
                                engagement=value_int,
                            )
                        )

                # Top queries
                top = data.get("top")
                if top is not None and not top.empty:
                    for _, row in top.head(10).iterrows():
                        query_text = row.get("query", "")
                        value = row.get("value", 0)

                        if not query_text:
                            continue

                        relevance = self._calculate_relevance(
                            query_text, brand_keywords
                        )

                        raw_data = json.dumps({
                            "keyword": keyword,
                            "query": query_text,
                            "value": str(value),
                            "type": "top",
                            "platform_context": "snapchat",
                        })

                        trends.append(
                            TrendItem(
                                topic=query_text,
                                source="snapchat",
                                source_detail=f"google_trends:snapchat_top:{keyword}",
                                raw_data=raw_data,
                                relevance_score=relevance,
                                engagement=int(value) if value else 0,
                            )
                        )

        except Exception as e:
            logger.warning(
                "snapchat_related_queries_failed",
                keywords=keywords,
                error=str(e),
            )

        # Related topics
        try:
            topics = pytrends.related_topics()
            for keyword, data in topics.items():
                if data is None:
                    continue

                rising = data.get("rising")
                if rising is not None and not rising.empty:
                    for _, row in rising.iterrows():
                        topic_title = row.get("topic_title", "")
                        value = row.get("value", 0)

                        if not topic_title:
                            continue

                        relevance = self._calculate_relevance(
                            topic_title, brand_keywords
                        )

                        raw_data = json.dumps({
                            "keyword": keyword,
                            "topic": topic_title,
                            "topic_type": row.get("topic_type", ""),
                            "value": str(value),
                            "type": "rising_topic",
                            "platform_context": "snapchat",
                        })

                        trends.append(
                            TrendItem(
                                topic=topic_title,
                                source="snapchat",
                                source_detail=f"google_trends:snapchat_topic:{keyword}",
                                raw_data=raw_data,
                                relevance_score=relevance,
                                engagement=int(value) if value else 0,
                            )
                        )

        except Exception as e:
            logger.warning(
                "snapchat_related_topics_failed",
                keywords=keywords,
                error=str(e),
            )

        return trends

    # ------------------------------------------------------------------
    # Google News RSS (fallback)
    # ------------------------------------------------------------------

    def _fetch_snapchat_news_trends(
        self,
        keywords: list[str],
        brand_keywords: list[str],
        brand_id: str,
    ) -> list[TrendItem]:
        """
        Fetches Snapchat-related news articles via Google News RSS.

        Searches for articles about Snapchat trends in the brand's niche.

        Parameters:
            keywords: Brand keywords to search with Snapchat context.
            brand_keywords: All brand keywords for relevance scoring.
            brand_id: Brand identifier for logging.

        Returns:
            List of TrendItem objects from news articles.

        Side effects:
            Makes HTTP requests to Google News RSS.
        """
        trends = []

        # Build Snapchat-focused search queries
        search_terms = []
        for kw in keywords[:5]:
            clean = (
                kw.replace(" snapchat", "")
                .replace("snapchat ", "")
                .replace(" spotlight", "")
                .replace("spotlight ", "")
                .replace(" snap", "")
                .strip()
            )
            search_terms.append(f"snapchat trending {clean}")

        for search_query in search_terms:
            try:
                article_trends = self._fetch_news_articles(
                    search_query, brand_keywords
                )
                trends.extend(article_trends)
                time.sleep(self.NEWS_DELAY)
            except Exception as e:
                logger.warning(
                    "snapchat_news_fetch_error",
                    query=search_query,
                    error=str(e),
                )

        return trends

    @retry_with_backoff(
        max_retries=2, base_delay=5.0, retry_on=(requests.RequestException,)
    )
    def _fetch_news_articles(
        self, search_query: str, brand_keywords: list[str]
    ) -> list[TrendItem]:
        """
        Fetches news articles for a single search query.

        Parameters:
            search_query: The search query to use.
            brand_keywords: All brand keywords for relevance scoring.

        Returns:
            List of TrendItem objects from news results.

        Side effects:
            Makes HTTP request to Google News RSS.
        """
        encoded = quote_plus(search_query)
        url = f"{self.GOOGLE_NEWS_URL}?q={encoded}&hl=en-US&gl=US&ceid=US:en"

        resp = self.session.get(url, timeout=15)
        resp.raise_for_status()

        trends = []

        try:
            root = ET.fromstring(resp.content)
            channel = root.find("channel")
            if channel is None:
                return []

            for item in channel.findall("item")[:5]:
                title = item.findtext("title", "")
                link = item.findtext("link", "")
                description = item.findtext("description", "")

                if not title:
                    continue

                clean_title = title
                if " - " in title:
                    clean_title = title.rsplit(" - ", 1)[0].strip()

                full_text = f"{clean_title} {description}"
                relevance = self._calculate_relevance(
                    full_text, brand_keywords
                )

                raw_data = json.dumps({
                    "query": search_query,
                    "title": clean_title,
                    "link": link,
                    "type": "news",
                    "platform_context": "snapchat",
                })

                trends.append(
                    TrendItem(
                        topic=clean_title,
                        source="snapchat",
                        source_detail=f"news:snapchat:{search_query}",
                        raw_data=raw_data,
                        relevance_score=relevance,
                        url=link,
                    )
                )

        except ET.ParseError as e:
            logger.warning(
                "snapchat_news_parse_error",
                query=search_query,
                error=str(e),
            )

        return trends

    # ------------------------------------------------------------------
    # Keyword extraction
    # ------------------------------------------------------------------

    def _extract_all_keywords(self, brand_config: dict) -> list[str]:
        """
        Extracts all keywords from brand config for relevance scoring.

        Parameters:
            brand_config: Full brand configuration.

        Returns:
            Deduplicated list of lowercase keywords.
        """
        keywords = []

        keywords.extend(brand_config.get("pillars", []))
        niche = brand_config.get("niche", "")
        if niche:
            keywords.extend(niche.lower().split())

        trend_sources = brand_config.get("trend_sources", {})
        keywords.extend(trend_sources.get("news_keywords", []))
        keywords.extend(trend_sources.get("google_trends_keywords", []))
        keywords.extend(trend_sources.get("snapchat_keywords", []))

        seen = set()
        clean = []
        for kw in keywords:
            kw_lower = kw.lower().strip()
            if kw_lower and kw_lower not in seen and len(kw_lower) > 2:
                seen.add(kw_lower)
                clean.append(kw_lower)

        return clean
