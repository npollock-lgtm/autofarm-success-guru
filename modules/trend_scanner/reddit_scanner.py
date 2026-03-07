"""
Reddit trend scanner for AutoFarm Zero — Success Guru Network v6.0.

Scans brand-specific subreddits for trending topics. Uses a three-tier
approach to handle cloud IP blocking:

1. **Direct API:** Tries Reddit's public JSON API first
   (``reddit.com/r/{sub}/hot.json``). Fast and accurate but often
   blocked from cloud IPs (OCI/AWS/GCP).

2. **Google Trends fallback:** If the direct API is blocked, queries
   Google Trends with Reddit-contextualized keywords (e.g.
   "stoicism reddit", "dark psychology reddit") to discover what
   people are searching for related to Reddit content.

3. **Google News RSS fallback:** Searches for news articles about
   Reddit trends in the brand's niche.

This design works out of the box from any cloud VM — no API keys,
no OAuth registration, no setup required.

Each brand has a list of subreddits defined in brands.json under
trend_sources.subreddits. The scanner uses these for direct API calls
and also derives Google Trends keywords from them.
"""

import os
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
from modules.compliance.rate_limit_manager import RateLimitManager
from modules.infrastructure.retry_handler import retry_with_backoff

logger = structlog.get_logger(__name__)


class RedditScanner(BaseScanner):
    """
    Scans Reddit for trending content topics.

    Tries the direct Reddit JSON API first. If blocked (403),
    automatically falls back to Google Trends + Google News RSS
    with Reddit-contextualized keywords.

    Attributes:
        PUBLIC_BASE_URL: Reddit public JSON API base URL.
        GOOGLE_NEWS_URL: Google News RSS endpoint.
        POSTS_PER_SUBREDDIT: Number of posts to fetch per subreddit.
        MIN_UPVOTES: Minimum upvotes to consider a post trending.
        REQUEST_DELAY: Seconds between Reddit API requests.
        PYTRENDS_DELAY: Seconds between Google Trends requests.
        NEWS_DELAY: Seconds between Google News RSS requests.
    """

    PUBLIC_BASE_URL: str = "https://www.reddit.com"
    GOOGLE_NEWS_URL: str = "https://news.google.com/rss/search"
    POSTS_PER_SUBREDDIT: int = 25
    MIN_UPVOTES: int = 50
    REQUEST_DELAY: float = 2.0
    PYTRENDS_DELAY: float = 5.0
    NEWS_DELAY: float = 3.0
    USER_AGENT: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )

    def __init__(self) -> None:
        """
        Initializes the Reddit scanner.

        Side effects:
            Creates an HTTP session with browser-like headers.
            Configures Squid proxy for direct API attempts.
            Checks pytrends availability for fallback scanning.
        """
        super().__init__()

        # Session for direct Reddit API
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": self.USER_AGENT,
            "Accept": "application/json",
        })

        # Route through Squid proxy (may help with cloud IP blocks)
        proxy_ip = os.getenv("PROXY_VM_INTERNAL_IP", "10.0.2.112")
        proxy_port = os.getenv("REDDIT_PROXY_PORT", "3128")
        proxy_url = f"http://{proxy_ip}:{proxy_port}"
        self.session.proxies = {
            "http": proxy_url,
            "https": proxy_url,
        }

        # News RSS session (separate, no proxy needed)
        self._news_session = requests.Session()
        self._news_session.headers.update({
            "User-Agent": "AutoFarm/6.0 Reddit News Scanner",
            "Accept": "application/rss+xml, application/xml, text/xml",
        })

        # Track whether direct API works (sticky per session)
        self._direct_api_blocked = False

        # pytrends fallback
        self._pytrends_available = False
        try:
            from pytrends.request import TrendReq
            self._TrendReq = TrendReq
            self._pytrends_available = True
        except ImportError:
            logger.info("pytrends_not_available_for_reddit_fallback")

        self._rate_limiter = RateLimitManager()

    def scan(self, brand_id: str, brand_config: dict) -> list[TrendItem]:
        """
        Scans for Reddit trends relevant to the brand.

        Tries the direct Reddit API first. If it returns 403 (IP blocked),
        automatically switches to Google Trends + News RSS fallback for
        the rest of the session.

        Parameters:
            brand_id: Brand identifier.
            brand_config: Full brand config from brands.json.

        Returns:
            List of TrendItem objects discovered.

        Side effects:
            Makes HTTP requests to Reddit and/or Google Trends/News.
            Stores trends in the database.
        """
        trend_sources = brand_config.get("trend_sources", {})
        subreddits = trend_sources.get("subreddits", [])
        keywords = self._extract_keywords(brand_config)

        if not subreddits:
            logger.warning("no_subreddits_configured", brand_id=brand_id)
            return []

        all_trends = []

        # --- Try direct Reddit API first (unless previously blocked) ---
        if not self._direct_api_blocked:
            direct_trends = self._scan_direct_api(
                subreddits, keywords, brand_id
            )
            all_trends.extend(direct_trends)

        # --- If direct API failed/blocked, use fallback sources ---
        if self._direct_api_blocked or not all_trends:
            fallback_trends = self._scan_fallback(
                subreddits, keywords, brand_config, brand_id
            )
            all_trends.extend(fallback_trends)

        # Deduplicate by topic
        seen_topics = set()
        unique_trends = []
        for trend in all_trends:
            topic_key = trend.topic.lower().strip()
            if topic_key not in seen_topics:
                seen_topics.add(topic_key)
                unique_trends.append(trend)

        # Sort by relevance and take top N
        unique_trends.sort(key=lambda t: t.relevance_score, reverse=True)
        top_trends = unique_trends[: self.MAX_TRENDS_PER_SCAN]

        # Store in database
        stored = self.store_trends(brand_id, top_trends)

        logger.info(
            "reddit_scan_complete",
            brand_id=brand_id,
            direct_api=not self._direct_api_blocked,
            subreddits_scanned=len(subreddits),
            trends_found=len(unique_trends),
            trends_stored=stored,
        )

        return top_trends

    # ------------------------------------------------------------------
    # Direct Reddit API
    # ------------------------------------------------------------------

    def _scan_direct_api(
        self,
        subreddits: list[str],
        keywords: list[str],
        brand_id: str,
    ) -> list[TrendItem]:
        """
        Attempts to fetch trends directly from Reddit's JSON API.

        Parameters:
            subreddits: List of subreddit names to scan.
            keywords: Brand keywords for relevance scoring.
            brand_id: Brand identifier for logging.

        Returns:
            List of TrendItem objects. Empty if API is blocked.

        Side effects:
            Sets self._direct_api_blocked = True on 403 response.
        """
        all_trends = []

        for subreddit in subreddits:
            try:
                hot_trends = self._fetch_subreddit(
                    subreddit, "hot", keywords, brand_id
                )
                all_trends.extend(hot_trends)
                time.sleep(self.REQUEST_DELAY)

                top_trends = self._fetch_subreddit(
                    subreddit, "top", keywords, brand_id,
                    time_filter="week",
                )
                all_trends.extend(top_trends)
                time.sleep(self.REQUEST_DELAY)

            except Exception as e:
                logger.error(
                    "reddit_direct_api_error",
                    brand_id=brand_id,
                    subreddit=subreddit,
                    error=str(e),
                )

            # Stop trying remaining subreddits if blocked
            if self._direct_api_blocked:
                break

        return all_trends

    def _fetch_subreddit(
        self,
        subreddit: str,
        sort: str,
        keywords: list[str],
        brand_id: str,
        time_filter: str = "day",
    ) -> list[TrendItem]:
        """
        Fetches posts from a single subreddit via the public JSON API.

        Parameters:
            subreddit: Subreddit name (without r/ prefix).
            sort: Sort order ('hot', 'top', 'new', 'rising').
            keywords: List of keywords for relevance scoring.
            brand_id: Brand identifier for logging.
            time_filter: Time filter for 'top' sort.

        Returns:
            List of TrendItem objects from this subreddit.

        Side effects:
            Makes HTTP request to Reddit.
            Sets self._direct_api_blocked on 403.
        """
        url = f"{self.PUBLIC_BASE_URL}/r/{subreddit}/{sort}.json"
        params = {
            "limit": self.POSTS_PER_SUBREDDIT,
            "raw_json": 1,
        }
        if sort == "top":
            params["t"] = time_filter

        try:
            response = self.session.get(url, params=params, timeout=15)
        except requests.RequestException as e:
            logger.warning(
                "reddit_request_failed",
                subreddit=subreddit,
                error=str(e),
            )
            return []

        # Handle 403 — cloud IP blocked
        if response.status_code == 403:
            self._direct_api_blocked = True
            logger.warning(
                "reddit_403_switching_to_fallback",
                subreddit=subreddit,
                msg="Direct Reddit API blocked from this IP. "
                    "Switching to Google Trends + News RSS fallback.",
            )
            return []

        # Handle rate limiting
        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 60))
            logger.warning(
                "reddit_rate_limited",
                subreddit=subreddit,
                retry_after=retry_after,
            )
            time.sleep(min(retry_after, 120))
            return []

        if response.status_code != 200:
            logger.warning(
                "reddit_unexpected_status",
                subreddit=subreddit,
                status=response.status_code,
            )
            return []

        try:
            data = response.json()
        except Exception:
            return []

        trends = []
        posts = data.get("data", {}).get("children", [])

        for post_wrapper in posts:
            post = post_wrapper.get("data", {})

            if post.get("stickied", False):
                continue
            if post.get("over_18", False):
                continue
            score = post.get("score", 0)
            if score < self.MIN_UPVOTES:
                continue

            title = post.get("title", "")
            selftext = post.get("selftext", "")[:500]
            full_text = f"{title} {selftext}"

            relevance = self._calculate_relevance(full_text, keywords)

            # Boost relevance based on engagement
            if score > 1000:
                relevance = min(relevance + 0.15, 1.0)
            elif score > 500:
                relevance = min(relevance + 0.1, 1.0)
            elif score > 200:
                relevance = min(relevance + 0.05, 1.0)

            raw_data = json.dumps({
                "subreddit": subreddit,
                "title": title,
                "selftext": selftext,
                "score": score,
                "num_comments": post.get("num_comments", 0),
                "url": post.get("url", ""),
                "permalink": post.get("permalink", ""),
                "created_utc": post.get("created_utc", 0),
            })

            trend = TrendItem(
                topic=title,
                source="reddit",
                source_detail=f"r/{subreddit}/{sort}",
                raw_data=raw_data,
                relevance_score=relevance,
                url=f"https://reddit.com{post.get('permalink', '')}",
                engagement=score,
            )
            trends.append(trend)

        return trends

    # ------------------------------------------------------------------
    # Fallback: Google Trends + News RSS
    # ------------------------------------------------------------------

    def _scan_fallback(
        self,
        subreddits: list[str],
        keywords: list[str],
        brand_config: dict,
        brand_id: str,
    ) -> list[TrendItem]:
        """
        Scans for Reddit trends via Google Trends and News RSS.

        Used when the direct Reddit API is blocked from cloud IPs.
        Builds Reddit-contextualized search queries from subreddit
        names and brand keywords.

        Parameters:
            subreddits: Subreddit names (used to build search queries).
            keywords: Brand keywords for relevance scoring.
            brand_config: Full brand config.
            brand_id: Brand identifier for logging.

        Returns:
            List of TrendItem objects from fallback sources.

        Side effects:
            Makes HTTP requests to Google Trends and News RSS.
        """
        logger.info(
            "reddit_using_fallback",
            brand_id=brand_id,
            msg="Using Google Trends + News RSS for Reddit trends",
        )

        all_trends = []

        # Build Reddit-focused keywords from subreddits + brand keywords
        reddit_keywords = self._build_reddit_keywords(
            subreddits, brand_config
        )

        # Google Trends with Reddit context
        if self._pytrends_available and reddit_keywords:
            gt_trends = self._fetch_reddit_google_trends(
                reddit_keywords, keywords, brand_id
            )
            all_trends.extend(gt_trends)

        # Google News RSS for Reddit trends
        news_trends = self._fetch_reddit_news_trends(
            subreddits, keywords, brand_id
        )
        all_trends.extend(news_trends)

        return all_trends

    def _build_reddit_keywords(
        self,
        subreddits: list[str],
        brand_config: dict,
    ) -> list[str]:
        """
        Builds Reddit-contextualized keywords for Google Trends.

        Combines subreddit names and brand niche keywords with
        "reddit" to create platform-specific search queries.

        Parameters:
            subreddits: List of subreddit names.
            brand_config: Full brand configuration.

        Returns:
            List of search keywords (max 5 for pytrends).
        """
        trend_sources = brand_config.get("trend_sources", {})
        gt_keywords = trend_sources.get("google_trends_keywords", [])

        # Build queries like "stoicism reddit", "dark psychology reddit"
        reddit_queries = []
        for kw in gt_keywords[:3]:
            reddit_queries.append(f"{kw} reddit")

        # Add top subreddit names as queries
        for sub in subreddits[:2]:
            reddit_queries.append(f"r/{sub}")

        return reddit_queries[:5]  # pytrends max 5 per request

    def _fetch_reddit_google_trends(
        self,
        reddit_keywords: list[str],
        brand_keywords: list[str],
        brand_id: str,
    ) -> list[TrendItem]:
        """
        Fetches Reddit-related search trends via pytrends.

        Parameters:
            reddit_keywords: Reddit-contextualized search keywords.
            brand_keywords: All brand keywords for relevance scoring.
            brand_id: Brand identifier for logging.

        Returns:
            List of TrendItem objects from Google Trends.

        Side effects:
            Makes HTTP requests to Google Trends.
        """
        try:
            return self._fetch_trends_batch(
                reddit_keywords, brand_keywords, brand_id
            )
        except Exception as e:
            logger.warning(
                "reddit_google_trends_error",
                brand_id=brand_id,
                error=str(e),
            )
            return []

    @retry_with_backoff(max_retries=2, base_delay=10.0, retry_on=(Exception,))
    def _fetch_trends_batch(
        self,
        keywords: list[str],
        brand_keywords: list[str],
        brand_id: str,
    ) -> list[TrendItem]:
        """
        Fetches a single batch of search trends via pytrends.

        Parameters:
            keywords: Batch of search keywords (max 5).
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
                            "platform_context": "reddit",
                        })

                        trends.append(
                            TrendItem(
                                topic=query_text,
                                source="reddit",
                                source_detail=f"google_trends:reddit:{keyword}",
                                raw_data=raw_data,
                                relevance_score=relevance,
                                engagement=value_int,
                            )
                        )

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
                            "platform_context": "reddit",
                        })

                        trends.append(
                            TrendItem(
                                topic=query_text,
                                source="reddit",
                                source_detail=f"google_trends:reddit_top:{keyword}",
                                raw_data=raw_data,
                                relevance_score=relevance,
                                engagement=int(value) if value else 0,
                            )
                        )

        except Exception as e:
            logger.warning(
                "reddit_trends_related_queries_failed",
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
                            "platform_context": "reddit",
                        })

                        trends.append(
                            TrendItem(
                                topic=topic_title,
                                source="reddit",
                                source_detail=f"google_trends:reddit_topic:{keyword}",
                                raw_data=raw_data,
                                relevance_score=relevance,
                                engagement=int(value) if value else 0,
                            )
                        )

        except Exception as e:
            logger.warning(
                "reddit_trends_related_topics_failed",
                keywords=keywords,
                error=str(e),
            )

        return trends

    # ------------------------------------------------------------------
    # Google News RSS fallback
    # ------------------------------------------------------------------

    def _fetch_reddit_news_trends(
        self,
        subreddits: list[str],
        brand_keywords: list[str],
        brand_id: str,
    ) -> list[TrendItem]:
        """
        Fetches Reddit-related news articles via Google News RSS.

        Builds search queries from subreddit names to find articles
        about what's trending on Reddit in the brand's niche.

        Parameters:
            subreddits: Subreddit names to build queries from.
            brand_keywords: All brand keywords for relevance scoring.
            brand_id: Brand identifier for logging.

        Returns:
            List of TrendItem objects from news articles.

        Side effects:
            Makes HTTP requests to Google News RSS.
        """
        trends = []

        # Build queries like "reddit stoicism trending", "reddit psychology popular"
        search_terms = []
        for sub in subreddits[:5]:
            search_terms.append(f"reddit {sub} trending")

        for search_query in search_terms:
            try:
                article_trends = self._fetch_news_articles(
                    search_query, brand_keywords
                )
                trends.extend(article_trends)
                time.sleep(self.NEWS_DELAY)
            except Exception as e:
                logger.warning(
                    "reddit_news_fetch_error",
                    query=search_query,
                    error=str(e),
                )

        return trends

    @retry_with_backoff(
        max_retries=2, base_delay=5.0,
        retry_on=(requests.RequestException,),
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

        resp = self._news_session.get(url, timeout=15)
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
                    "platform_context": "reddit",
                })

                trends.append(
                    TrendItem(
                        topic=clean_title,
                        source="reddit",
                        source_detail=f"news:reddit:{search_query}",
                        raw_data=raw_data,
                        relevance_score=relevance,
                        url=link,
                    )
                )

        except ET.ParseError as e:
            logger.warning(
                "reddit_news_parse_error",
                query=search_query,
                error=str(e),
            )

        return trends

    # ------------------------------------------------------------------
    # Keyword extraction
    # ------------------------------------------------------------------

    def _extract_keywords(self, brand_config: dict) -> list[str]:
        """
        Extracts relevance keywords from brand configuration.

        Parameters:
            brand_config: Full brand config dict.

        Returns:
            List of keywords for relevance scoring.
        """
        keywords = []

        keywords.extend(brand_config.get("pillars", []))

        niche = brand_config.get("niche", "")
        if niche:
            keywords.extend(niche.lower().split())

        trend_sources = brand_config.get("trend_sources", {})
        keywords.extend(trend_sources.get("news_keywords", []))
        keywords.extend(trend_sources.get("google_trends_keywords", []))

        seen = set()
        clean_keywords = []
        for kw in keywords:
            kw_lower = kw.lower().strip()
            if kw_lower and kw_lower not in seen and len(kw_lower) > 2:
                seen.add(kw_lower)
                clean_keywords.append(kw_lower)

        return clean_keywords
