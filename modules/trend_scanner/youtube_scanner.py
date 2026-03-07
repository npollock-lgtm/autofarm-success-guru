"""
YouTube trend scanner for AutoFarm Zero — Success Guru Network v6.0.

Scans YouTube for trending videos relevant to each brand's niche.
Uses two data sources:

1. **Primary:** YouTube Data API v3 — fetches trending videos by category.
   Free tier provides 10,000 quota units/day. ``videos.list`` with
   ``chart=mostPopular`` costs only 1 unit per call.  Requires a
   ``YOUTUBE_API_KEY`` environment variable (free from Google Cloud Console).

2. **Fallback:** pytrends with ``gprop='youtube'`` — discovers what people
   are searching for *on YouTube* for the brand's keywords. Works without
   any API key.

Each brand has ``youtube_category_ids`` and ``youtube_keywords`` defined
in ``brands.json`` under ``trend_sources``.
"""

import json
import os
import time
import logging
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote_plus

import requests
import structlog

from modules.trend_scanner.base_scanner import BaseScanner, TrendItem
from modules.infrastructure.retry_handler import retry_with_backoff

logger = structlog.get_logger(__name__)


class YouTubeScanner(BaseScanner):
    """
    Scans YouTube for trending video topics.

    Uses the YouTube Data API v3 (primary) to discover trending videos
    in categories relevant to each brand.  Falls back to Google Trends
    YouTube search data (via pytrends) when no API key is configured.

    Attributes:
        API_BASE_URL: YouTube Data API v3 base URL.
        REQUEST_DELAY: Seconds between API calls.
        MAX_RESULTS_PER_CATEGORY: Videos fetched per category.
        MAX_DAILY_QUOTA: YouTube API free-tier daily quota limit.
    """

    API_BASE_URL: str = "https://www.googleapis.com/youtube/v3"
    REQUEST_DELAY: float = 2.0
    MAX_RESULTS_PER_CATEGORY: int = 10
    MAX_DAILY_QUOTA: int = 10000
    NEWS_REQUEST_DELAY: float = 3.0
    GOOGLE_NEWS_URL: str = "https://news.google.com/rss/search"

    def __init__(self) -> None:
        """
        Initializes the YouTube scanner.

        Side effects:
            Reads YOUTUBE_API_KEY from environment.
            Attempts to import pytrends for fallback scanning.
            Creates HTTP session.
        """
        super().__init__()
        self._api_key = os.getenv("YOUTUBE_API_KEY", "")
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "AutoFarm/6.0 YouTube Scanner",
            "Accept": "application/json",
        })
        self._daily_quota_used = 0
        self._quota_reset_date = datetime.now(timezone.utc).date()

        # pytrends fallback
        self._pytrends_available = False
        try:
            from pytrends.request import TrendReq
            self._TrendReq = TrendReq
            self._pytrends_available = True
        except ImportError:
            logger.info("pytrends_not_available_for_youtube_fallback")

    def scan(self, brand_id: str, brand_config: dict) -> list[TrendItem]:
        """
        Scans YouTube for topics relevant to the brand.

        Parameters:
            brand_id: Brand identifier.
            brand_config: Full brand config from brands.json.

        Returns:
            List of TrendItem objects from YouTube sources.

        Side effects:
            Makes HTTP requests to YouTube API and/or Google Trends.
        """
        trend_sources = brand_config.get("trend_sources", {})
        category_ids = trend_sources.get("youtube_category_ids", [])
        yt_keywords = trend_sources.get("youtube_keywords", [])

        if not category_ids and not yt_keywords:
            logger.info("no_youtube_config", brand_id=brand_id)
            return []

        all_trends = []
        brand_keywords = self._extract_all_keywords(brand_config)

        # Primary: YouTube Data API v3
        if self._api_key and category_ids:
            api_trends = self._fetch_trending_videos(
                category_ids, brand_keywords, brand_id
            )
            all_trends.extend(api_trends)

        # Fallback / supplement: pytrends with gprop='youtube'
        if self._pytrends_available and yt_keywords:
            gt_trends = self._fetch_youtube_google_trends(
                yt_keywords, brand_keywords, brand_id
            )
            all_trends.extend(gt_trends)

        # If neither primary nor pytrends produced results, try news
        if not all_trends and yt_keywords:
            news_trends = self._fetch_youtube_news_trends(
                yt_keywords, brand_keywords, brand_id
            )
            all_trends.extend(news_trends)

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
            "youtube_scan_complete",
            brand_id=brand_id,
            api_key_set=bool(self._api_key),
            trends_found=len(unique_trends),
            trends_stored=stored,
        )

        return top_trends

    # ------------------------------------------------------------------
    # YouTube Data API v3
    # ------------------------------------------------------------------

    def _fetch_trending_videos(
        self,
        category_ids: list[int],
        brand_keywords: list[str],
        brand_id: str,
    ) -> list[TrendItem]:
        """
        Fetches trending videos from YouTube Data API v3.

        Parameters:
            category_ids: YouTube video category IDs to scan.
            brand_keywords: Keywords for relevance scoring.
            brand_id: Brand identifier for logging.

        Returns:
            List of TrendItem objects from trending videos.

        Side effects:
            Makes HTTP requests to YouTube Data API.
            Increments daily quota counter.
        """
        # Reset quota counter if new day
        today = datetime.now(timezone.utc).date()
        if today != self._quota_reset_date:
            self._daily_quota_used = 0
            self._quota_reset_date = today

        trends = []

        for cat_id in category_ids:
            if self._daily_quota_used >= self.MAX_DAILY_QUOTA - 100:
                logger.warning(
                    "youtube_quota_near_limit",
                    used=self._daily_quota_used,
                    limit=self.MAX_DAILY_QUOTA,
                )
                break

            try:
                cat_trends = self._fetch_category_trending(
                    cat_id, brand_keywords, brand_id
                )
                trends.extend(cat_trends)
                time.sleep(self.REQUEST_DELAY)
            except Exception as e:
                logger.error(
                    "youtube_api_category_error",
                    brand_id=brand_id,
                    category_id=cat_id,
                    error=str(e),
                )

        return trends

    @retry_with_backoff(
        max_retries=2, base_delay=5.0, retry_on=(requests.RequestException,)
    )
    def _fetch_category_trending(
        self,
        category_id: int,
        brand_keywords: list[str],
        brand_id: str,
    ) -> list[TrendItem]:
        """
        Fetches trending videos for a single YouTube category.

        Parameters:
            category_id: YouTube video category ID.
            brand_keywords: Keywords for relevance scoring.
            brand_id: Brand identifier for logging.

        Returns:
            List of TrendItem objects for this category.

        Side effects:
            Makes one HTTP request to YouTube API (1 quota unit).
        """
        url = f"{self.API_BASE_URL}/videos"
        params = {
            "part": "snippet,statistics",
            "chart": "mostPopular",
            "regionCode": "US",
            "videoCategoryId": str(category_id),
            "maxResults": self.MAX_RESULTS_PER_CATEGORY,
            "key": self._api_key,
        }

        resp = self.session.get(url, params=params, timeout=15)
        self._daily_quota_used += 1

        if resp.status_code == 403:
            logger.warning(
                "youtube_api_quota_exceeded",
                status=resp.status_code,
                response=resp.text[:300],
            )
            return []

        resp.raise_for_status()
        data = resp.json()

        trends = []
        items = data.get("items", [])

        for item in items:
            snippet = item.get("snippet", {})
            stats = item.get("statistics", {})

            title = snippet.get("title", "")
            description = snippet.get("description", "")
            channel = snippet.get("channelTitle", "")
            video_id = item.get("id", "")

            if not title:
                continue

            # Relevance scoring
            full_text = f"{title} {description[:500]} {channel}"
            relevance = self._calculate_relevance(full_text, brand_keywords)

            # View count boost
            view_count = int(stats.get("viewCount", 0))
            if view_count > 1_000_000:
                relevance = min(relevance + 0.15, 1.0)
            elif view_count > 100_000:
                relevance = min(relevance + 0.10, 1.0)
            elif view_count > 10_000:
                relevance = min(relevance + 0.05, 1.0)

            raw_data = json.dumps(
                {
                    "video_id": video_id,
                    "title": title,
                    "channel": channel,
                    "category_id": category_id,
                    "view_count": view_count,
                    "like_count": int(stats.get("likeCount", 0)),
                    "comment_count": int(stats.get("commentCount", 0)),
                    "published_at": snippet.get("publishedAt", ""),
                }
            )

            trends.append(
                TrendItem(
                    topic=title,
                    source="youtube",
                    source_detail=f"trending:cat_{category_id}",
                    raw_data=raw_data,
                    relevance_score=relevance,
                    url=f"https://www.youtube.com/watch?v={video_id}",
                    engagement=view_count,
                )
            )

        logger.info(
            "youtube_category_fetched",
            brand_id=brand_id,
            category_id=category_id,
            videos_found=len(trends),
        )

        return trends

    # ------------------------------------------------------------------
    # Google Trends fallback (gprop='youtube')
    # ------------------------------------------------------------------

    def _fetch_youtube_google_trends(
        self,
        keywords: list[str],
        brand_keywords: list[str],
        brand_id: str,
    ) -> list[TrendItem]:
        """
        Fetches YouTube-specific search trends via pytrends.

        Uses ``gprop='youtube'`` to filter Google Trends results
        to YouTube searches only.

        Parameters:
            keywords: Seed keywords to query.
            brand_keywords: All brand keywords for relevance scoring.
            brand_id: Brand identifier for logging.

        Returns:
            List of TrendItem objects from Google Trends YouTube data.

        Side effects:
            Makes HTTP requests to Google Trends.
        """
        trends = []
        max_per_request = 5  # Google Trends limit

        for i in range(0, len(keywords), max_per_request):
            batch = keywords[i : i + max_per_request]

            try:
                batch_trends = self._fetch_youtube_trends_batch(
                    batch, brand_keywords, brand_id
                )
                trends.extend(batch_trends)
                time.sleep(5.0)  # Conservative delay for pytrends
            except Exception as e:
                logger.warning(
                    "youtube_google_trends_batch_error",
                    brand_id=brand_id,
                    keywords=batch,
                    error=str(e),
                )

        return trends

    @retry_with_backoff(max_retries=2, base_delay=10.0, retry_on=(Exception,))
    def _fetch_youtube_trends_batch(
        self,
        keywords: list[str],
        brand_keywords: list[str],
        brand_id: str,
    ) -> list[TrendItem]:
        """
        Fetches a single batch of YouTube search trends via pytrends.

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
            gprop="youtube",  # Filter to YouTube searches
        )

        trends = []

        # Related queries
        try:
            related = pytrends.related_queries()
            for keyword, data in related.items():
                if data is None:
                    continue

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

                        raw_data = json.dumps(
                            {
                                "keyword": keyword,
                                "query": query_text,
                                "value": str(value),
                                "type": "rising",
                                "gprop": "youtube",
                            }
                        )

                        trends.append(
                            TrendItem(
                                topic=query_text,
                                source="youtube",
                                source_detail=f"google_trends:youtube:{keyword}",
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

                        raw_data = json.dumps(
                            {
                                "keyword": keyword,
                                "query": query_text,
                                "value": str(value),
                                "type": "top",
                                "gprop": "youtube",
                            }
                        )

                        trends.append(
                            TrendItem(
                                topic=query_text,
                                source="youtube",
                                source_detail=f"google_trends:youtube_top:{keyword}",
                                raw_data=raw_data,
                                relevance_score=relevance,
                                engagement=int(value) if value else 0,
                            )
                        )

        except Exception as e:
            logger.warning(
                "youtube_related_queries_failed",
                keywords=keywords,
                error=str(e),
            )

        return trends

    # ------------------------------------------------------------------
    # Google News RSS fallback (YouTube trend articles)
    # ------------------------------------------------------------------

    @retry_with_backoff(
        max_retries=2, base_delay=5.0, retry_on=(requests.RequestException,)
    )
    def _fetch_youtube_news_trends(
        self,
        keywords: list[str],
        brand_keywords: list[str],
        brand_id: str,
    ) -> list[TrendItem]:
        """
        Fetches YouTube-related news articles via Google News RSS.

        Searches for articles about YouTube trends in the brand's niche.

        Parameters:
            keywords: Brand keywords to search with YouTube context.
            brand_keywords: All brand keywords for relevance scoring.
            brand_id: Brand identifier for logging.

        Returns:
            List of TrendItem objects from news articles.

        Side effects:
            Makes HTTP requests to Google News RSS.
        """
        import xml.etree.ElementTree as ET

        trends = []
        news_session = requests.Session()
        news_session.headers.update(
            {
                "User-Agent": "AutoFarm/6.0 YouTube News Scanner",
                "Accept": "application/rss+xml, application/xml, text/xml",
            }
        )

        for keyword in keywords[:5]:
            search_query = f"youtube trending {keyword}"
            encoded = quote_plus(search_query)
            url = f"{self.GOOGLE_NEWS_URL}?q={encoded}&hl=en-US&gl=US&ceid=US:en"

            try:
                resp = news_session.get(url, timeout=15)
                resp.raise_for_status()

                root = ET.fromstring(resp.content)
                channel = root.find("channel")
                if channel is None:
                    continue

                for item in channel.findall("item")[:5]:
                    title = item.findtext("title", "")
                    link = item.findtext("link", "")
                    description = item.findtext("description", "")

                    if not title:
                        continue

                    # Clean title
                    clean_title = title
                    if " - " in title:
                        clean_title = title.rsplit(" - ", 1)[0].strip()

                    full_text = f"{clean_title} {description}"
                    relevance = self._calculate_relevance(
                        full_text, brand_keywords
                    )

                    raw_data = json.dumps(
                        {
                            "keyword": keyword,
                            "title": clean_title,
                            "link": link,
                            "type": "news",
                            "platform_context": "youtube",
                        }
                    )

                    trends.append(
                        TrendItem(
                            topic=clean_title,
                            source="youtube",
                            source_detail=f"news:youtube:{keyword}",
                            raw_data=raw_data,
                            relevance_score=relevance,
                            url=link,
                        )
                    )

                time.sleep(self.NEWS_REQUEST_DELAY)

            except Exception as e:
                logger.warning(
                    "youtube_news_fetch_error",
                    keyword=keyword,
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
        keywords.extend(trend_sources.get("youtube_keywords", []))

        seen = set()
        clean = []
        for kw in keywords:
            kw_lower = kw.lower().strip()
            if kw_lower and kw_lower not in seen and len(kw_lower) > 2:
                seen.add(kw_lower)
                clean.append(kw_lower)

        return clean
