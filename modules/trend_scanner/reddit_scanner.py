"""
Reddit trend scanner for AutoFarm Zero — Success Guru Network v6.0.

Scans brand-specific subreddits for trending topics. Supports two
authentication modes:

1. **OAuth2 (recommended):** Uses Reddit's official OAuth API via a free
   "script" app registered at https://www.reddit.com/prefs/apps.
   Provides 60 requests/minute and works from any IP (including cloud
   providers like OCI/AWS/GCP that are blocked by the public API).
   Requires env vars: ``REDDIT_CLIENT_ID``, ``REDDIT_CLIENT_SECRET``,
   ``REDDIT_USERNAME``, ``REDDIT_PASSWORD``.

2. **Public JSON API (fallback):** Uses ``reddit.com/r/{sub}/hot.json``
   endpoints — no auth required but limited to 10 req/min and often
   blocked from cloud IPs.

Each brand has a list of subreddits defined in brands.json under
trend_sources.subreddits. The scanner fetches top and hot posts,
scores them for relevance, and stores the best as trend items.
"""

import os
import json
import time
import logging
from datetime import datetime, timezone, timedelta
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

    Uses Reddit's OAuth2 API (primary) or public JSON API (fallback)
    to fetch hot and top posts from brand-relevant subreddits.

    OAuth2 provides 60 req/min and works from cloud IPs.
    Public API is limited to 10 req/min and may be IP-blocked.

    Attributes:
        OAUTH_TOKEN_URL: Reddit OAuth2 token endpoint.
        OAUTH_BASE_URL: Authenticated API base URL.
        PUBLIC_BASE_URL: Public JSON API base URL.
        POSTS_PER_SUBREDDIT: Number of posts to fetch per subreddit.
        MIN_UPVOTES: Minimum upvotes to consider a post trending.
        REQUEST_DELAY: Seconds between requests (rate limiting).
    """

    OAUTH_TOKEN_URL: str = "https://www.reddit.com/api/v1/access_token"
    OAUTH_BASE_URL: str = "https://oauth.reddit.com"
    PUBLIC_BASE_URL: str = "https://www.reddit.com"
    POSTS_PER_SUBREDDIT: int = 25
    MIN_UPVOTES: int = 50
    REQUEST_DELAY: float = 2.0  # Reddit rate limit: 60 req/min (OAuth)
    USER_AGENT: str = "AutoFarm/6.0 TrendScanner (by /u/autofarm_bot)"

    def __init__(self) -> None:
        """
        Initializes the Reddit scanner.

        Attempts OAuth2 authentication first. Falls back to the public
        JSON API if credentials are not configured.

        Side effects:
            Creates an HTTP session with appropriate headers.
            Attempts to obtain an OAuth2 access token.
            Configures Squid proxy for public API fallback.
        """
        super().__init__()

        # OAuth2 credentials from environment
        self._client_id = os.getenv("REDDIT_CLIENT_ID", "")
        self._client_secret = os.getenv("REDDIT_CLIENT_SECRET", "")
        self._username = os.getenv("REDDIT_USERNAME", "")
        self._password = os.getenv("REDDIT_PASSWORD", "")

        # Token state
        self._access_token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None
        self._oauth_available = False

        # Create session
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": self.USER_AGENT,
            "Accept": "application/json",
        })

        # Try OAuth2 authentication
        if self._client_id and self._client_secret:
            self._authenticate()
        else:
            logger.info(
                "reddit_oauth_not_configured",
                msg="Set REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, "
                    "REDDIT_USERNAME, REDDIT_PASSWORD for OAuth2. "
                    "Falling back to public API (may be IP-blocked).",
            )
            # Public API fallback: route through Squid proxy
            proxy_ip = os.getenv("PROXY_VM_INTERNAL_IP", "10.0.2.112")
            proxy_port = os.getenv("REDDIT_PROXY_PORT", "3128")
            proxy_url = f"http://{proxy_ip}:{proxy_port}"
            self.session.proxies = {
                "http": proxy_url,
                "https": proxy_url,
            }

        self._rate_limiter = RateLimitManager()

    # ------------------------------------------------------------------
    # OAuth2 authentication
    # ------------------------------------------------------------------

    def _authenticate(self) -> bool:
        """
        Obtains an OAuth2 access token from Reddit.

        Uses the 'password' grant type for script-type apps, or falls
        back to 'client_credentials' if no username/password provided.

        Returns:
            True if authentication succeeded.

        Side effects:
            Sets self._access_token and self._token_expires_at.
            Updates session Authorization header.
        """
        auth = (self._client_id, self._client_secret)

        # Prefer password grant (script app) for full access
        if self._username and self._password:
            data = {
                "grant_type": "password",
                "username": self._username,
                "password": self._password,
            }
        else:
            # Application-only OAuth (no user context, read-only)
            data = {
                "grant_type": "client_credentials",
            }

        try:
            resp = requests.post(
                self.OAUTH_TOKEN_URL,
                auth=auth,
                data=data,
                headers={"User-Agent": self.USER_AGENT},
                timeout=15,
            )

            if resp.status_code == 200:
                token_data = resp.json()
                self._access_token = token_data.get("access_token")
                expires_in = token_data.get("expires_in", 3600)

                self._token_expires_at = (
                    datetime.now(timezone.utc)
                    + timedelta(seconds=expires_in - 60)  # Refresh 60s early
                )

                # Set auth header on the session
                self.session.headers["Authorization"] = (
                    f"Bearer {self._access_token}"
                )
                self._oauth_available = True

                grant = "password" if self._username else "client_credentials"
                logger.info(
                    "reddit_oauth_authenticated",
                    grant_type=grant,
                    expires_in=expires_in,
                )
                return True
            else:
                logger.error(
                    "reddit_oauth_failed",
                    status=resp.status_code,
                    response=resp.text[:300],
                )
                return False

        except Exception as e:
            logger.error("reddit_oauth_error", error=str(e))
            return False

    def _ensure_token_valid(self) -> None:
        """
        Refreshes the OAuth2 token if it has expired.

        Side effects:
            May re-authenticate and update the session headers.
        """
        if not self._oauth_available:
            return

        if (
            self._token_expires_at
            and datetime.now(timezone.utc) >= self._token_expires_at
        ):
            logger.info("reddit_oauth_token_expired_refreshing")
            self._authenticate()

    # ------------------------------------------------------------------
    # Scan
    # ------------------------------------------------------------------

    def scan(self, brand_id: str, brand_config: dict) -> list[TrendItem]:
        """
        Scans all subreddits configured for a brand.

        Parameters:
            brand_id: Brand identifier.
            brand_config: Full brand config from brands.json.

        Returns:
            List of TrendItem objects discovered across all subreddits.

        Side effects:
            Makes HTTP requests to Reddit's API (OAuth or public).
            Stores trends in the database.
        """
        trend_sources = brand_config.get("trend_sources", {})
        subreddits = trend_sources.get("subreddits", [])
        keywords = self._extract_keywords(brand_config)

        if not subreddits:
            logger.warning("no_subreddits_configured", brand_id=brand_id)
            return []

        # Ensure token is fresh
        self._ensure_token_valid()

        all_trends = []

        for subreddit in subreddits:
            try:
                # Fetch hot posts
                hot_trends = self._fetch_subreddit(
                    subreddit, "hot", keywords, brand_id
                )
                all_trends.extend(hot_trends)

                time.sleep(self.REQUEST_DELAY)

                # Fetch top posts from the past week
                top_trends = self._fetch_subreddit(
                    subreddit, "top", keywords, brand_id,
                    time_filter="week",
                )
                all_trends.extend(top_trends)

                time.sleep(self.REQUEST_DELAY)

            except Exception as e:
                logger.error(
                    "reddit_scan_error",
                    brand_id=brand_id,
                    subreddit=subreddit,
                    error=str(e),
                )

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
            oauth=self._oauth_available,
            subreddits_scanned=len(subreddits),
            trends_found=len(unique_trends),
            trends_stored=stored,
        )

        return top_trends

    # ------------------------------------------------------------------
    # Fetch subreddit
    # ------------------------------------------------------------------

    @retry_with_backoff(
        max_retries=2, base_delay=5.0,
        retry_on=(requests.RequestException,),
    )
    def _fetch_subreddit(
        self,
        subreddit: str,
        sort: str,
        keywords: list[str],
        brand_id: str,
        time_filter: str = "day",
    ) -> list[TrendItem]:
        """
        Fetches posts from a single subreddit.

        Uses oauth.reddit.com when authenticated, falls back to
        www.reddit.com/r/{sub}/{sort}.json for public API.

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
        """
        # Choose endpoint based on auth status
        if self._oauth_available:
            url = f"{self.OAUTH_BASE_URL}/r/{subreddit}/{sort}"
        else:
            url = f"{self.PUBLIC_BASE_URL}/r/{subreddit}/{sort}.json"

        params = {
            "limit": self.POSTS_PER_SUBREDDIT,
            "raw_json": 1,
        }
        if sort == "top":
            params["t"] = time_filter

        response = self.session.get(url, params=params, timeout=15)

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

        # Handle auth failure — retry with re-auth
        if response.status_code == 401 and self._oauth_available:
            logger.warning("reddit_oauth_401_reauth")
            self._authenticate()
            response = self.session.get(url, params=params, timeout=15)

        # Handle 403 — cloud IP likely blocked (public API)
        if response.status_code == 403:
            logger.warning(
                "reddit_403_blocked",
                subreddit=subreddit,
                oauth=self._oauth_available,
                msg="Reddit blocked this request. "
                    "Set REDDIT_CLIENT_ID etc. for OAuth2 access.",
            )
            return []

        response.raise_for_status()
        data = response.json()

        trends = []
        posts = data.get("data", {}).get("children", [])

        for post_wrapper in posts:
            post = post_wrapper.get("data", {})

            # Filter out non-relevant posts
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

        # From pillars
        keywords.extend(brand_config.get("pillars", []))

        # From niche
        niche = brand_config.get("niche", "")
        if niche:
            keywords.extend(niche.lower().split())

        # From trend sources keywords
        trend_sources = brand_config.get("trend_sources", {})
        keywords.extend(trend_sources.get("news_keywords", []))
        keywords.extend(trend_sources.get("google_trends_keywords", []))

        # Deduplicate and clean
        seen = set()
        clean_keywords = []
        for kw in keywords:
            kw_lower = kw.lower().strip()
            if kw_lower and kw_lower not in seen and len(kw_lower) > 2:
                seen.add(kw_lower)
                clean_keywords.append(kw_lower)

        return clean_keywords
