"""
Reddit trend scanner for AutoFarm Zero — Success Guru Network v6.0.

Scans brand-specific subreddits for trending topics. Uses Reddit's
public JSON API (no authentication required for reading public data).
Rate limited to 60 requests per minute per Reddit's API rules.

Each brand has a list of subreddits defined in brands.json under
trend_sources.subreddits. The scanner fetches top and hot posts,
scores them for relevance, and stores the best as trend items.
"""

import os
import json
import time
import logging
from datetime import datetime, timezone
from typing import Optional

import requests
import structlog

from modules.trend_scanner.base_scanner import BaseScanner, TrendItem
from modules.compliance.rate_limit_manager import RateLimitManager
from modules.infrastructure.retry_handler import retry_with_backoff

logger = structlog.get_logger(__name__)


class RedditScanner(BaseScanner):
    """
    Scans Reddit subreddits for trending content topics.

    Uses Reddit's public JSON API endpoints to fetch hot and top posts
    from brand-relevant subreddits. No authentication required for
    public subreddit data.

    Attributes:
        BASE_URL: Reddit JSON API base URL.
        POSTS_PER_SUBREDDIT: Number of posts to fetch per subreddit.
        MIN_UPVOTES: Minimum upvotes to consider a post trending.
        USER_AGENT: User agent string for Reddit API compliance.
        REQUEST_DELAY: Seconds between requests (rate limiting).
    """

    BASE_URL: str = "https://www.reddit.com"
    POSTS_PER_SUBREDDIT: int = 25
    MIN_UPVOTES: int = 50
    USER_AGENT: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    REQUEST_DELAY: float = 2.0  # Reddit rate limit: 60 req/min

    def __init__(self) -> None:
        """
        Initializes the Reddit scanner.

        Side effects:
            Creates an HTTP session with appropriate headers.
            Initializes rate limit manager.
            Configures Squid proxy if available (Reddit blocks cloud IPs).
        """
        super().__init__()
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': self.USER_AGENT,
            'Accept': 'application/json',
        })

        # Reddit blocks cloud provider IPs (OCI, AWS, GCP).
        # Route through Squid proxy on proxy VM for a residential-like IP.
        proxy_ip = os.getenv("PROXY_VM_INTERNAL_IP", "10.0.2.112")
        proxy_port = os.getenv("REDDIT_PROXY_PORT", "3128")
        proxy_url = f"http://{proxy_ip}:{proxy_port}"
        self.session.proxies = {
            'http': proxy_url,
            'https': proxy_url,
        }

        self._rate_limiter = RateLimitManager()

    def scan(self, brand_id: str, brand_config: dict) -> list[TrendItem]:
        """
        Scans all subreddits configured for a brand.

        Parameters:
            brand_id: Brand identifier.
            brand_config: Full brand config from brands.json.

        Returns:
            List of TrendItem objects discovered across all subreddits.

        Side effects:
            Makes HTTP requests to Reddit's JSON API.
            Stores trends in the database.
            Rate-limited to avoid Reddit rate limits.
        """
        trend_sources = brand_config.get('trend_sources', {})
        subreddits = trend_sources.get('subreddits', [])
        keywords = self._extract_keywords(brand_config)

        if not subreddits:
            logger.warning("no_subreddits_configured",
                            brand_id=brand_id)
            return []

        all_trends = []

        for subreddit in subreddits:
            try:
                # Fetch hot posts
                hot_trends = self._fetch_subreddit(
                    subreddit, 'hot', keywords, brand_id
                )
                all_trends.extend(hot_trends)

                # Brief delay between subreddits
                time.sleep(self.REQUEST_DELAY)

                # Fetch top posts from the past week
                top_trends = self._fetch_subreddit(
                    subreddit, 'top', keywords, brand_id,
                    time_filter='week'
                )
                all_trends.extend(top_trends)

                time.sleep(self.REQUEST_DELAY)

            except Exception as e:
                logger.error("reddit_scan_error",
                              brand_id=brand_id,
                              subreddit=subreddit,
                              error=str(e))

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
        top_trends = unique_trends[:self.MAX_TRENDS_PER_SCAN]

        # Store in database
        stored = self.store_trends(brand_id, top_trends)

        logger.info("reddit_scan_complete",
                      brand_id=brand_id,
                      subreddits_scanned=len(subreddits),
                      trends_found=len(unique_trends),
                      trends_stored=stored)

        return top_trends

    @retry_with_backoff(max_retries=2, base_delay=5.0,
                        retry_on=(requests.RequestException,))
    def _fetch_subreddit(self, subreddit: str, sort: str,
                          keywords: list[str],
                          brand_id: str,
                          time_filter: str = 'day') -> list[TrendItem]:
        """
        Fetches posts from a single subreddit.

        Parameters:
            subreddit: Subreddit name (without r/ prefix).
            sort: Sort order ('hot', 'top', 'new', 'rising').
            keywords: List of keywords for relevance scoring.
            brand_id: Brand identifier for logging.
            time_filter: Time filter for 'top' sort ('hour', 'day', 'week', 'month').

        Returns:
            List of TrendItem objects from this subreddit.

        Side effects:
            Makes HTTP request to Reddit.

        Raises:
            requests.RequestException: On HTTP errors.
        """
        url = f"{self.BASE_URL}/r/{subreddit}/{sort}.json"
        params = {
            'limit': self.POSTS_PER_SUBREDDIT,
            'raw_json': 1,
        }
        if sort == 'top':
            params['t'] = time_filter

        response = self.session.get(url, params=params, timeout=15)

        # Handle rate limiting
        if response.status_code == 429:
            retry_after = int(response.headers.get('Retry-After', 60))
            logger.warning("reddit_rate_limited",
                            subreddit=subreddit,
                            retry_after=retry_after)
            time.sleep(min(retry_after, 120))
            return []

        response.raise_for_status()
        data = response.json()

        trends = []
        posts = data.get('data', {}).get('children', [])

        for post_wrapper in posts:
            post = post_wrapper.get('data', {})

            # Filter out non-relevant posts
            if post.get('stickied', False):
                continue
            if post.get('over_18', False):
                continue
            score = post.get('score', 0)
            if score < self.MIN_UPVOTES:
                continue

            title = post.get('title', '')
            selftext = post.get('selftext', '')[:500]
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
                'subreddit': subreddit,
                'title': title,
                'selftext': selftext,
                'score': score,
                'num_comments': post.get('num_comments', 0),
                'url': post.get('url', ''),
                'permalink': post.get('permalink', ''),
                'created_utc': post.get('created_utc', 0),
            })

            trend = TrendItem(
                topic=title,
                source='reddit',
                source_detail=f"r/{subreddit}/{sort}",
                raw_data=raw_data,
                relevance_score=relevance,
                url=f"https://reddit.com{post.get('permalink', '')}",
                engagement=score,
            )
            trends.append(trend)

        return trends

    def _extract_keywords(self, brand_config: dict) -> list[str]:
        """
        Extracts relevance keywords from brand configuration.

        Parameters:
            brand_config: Full brand config dict.

        Returns:
            List of keywords for relevance scoring.
        """
        keywords = []

        # From pillars
        keywords.extend(brand_config.get('pillars', []))

        # From niche
        niche = brand_config.get('niche', '')
        if niche:
            keywords.extend(niche.lower().split())

        # From trend sources keywords
        trend_sources = brand_config.get('trend_sources', {})
        keywords.extend(trend_sources.get('news_keywords', []))
        keywords.extend(trend_sources.get('google_trends_keywords', []))

        # Deduplicate and clean
        seen = set()
        clean_keywords = []
        for kw in keywords:
            kw_lower = kw.lower().strip()
            if kw_lower and kw_lower not in seen and len(kw_lower) > 2:
                seen.add(kw_lower)
                clean_keywords.append(kw_lower)

        return clean_keywords
