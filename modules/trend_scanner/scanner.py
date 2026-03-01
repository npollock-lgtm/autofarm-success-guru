"""
Trend scanner coordinator for AutoFarm Zero — Success Guru Network v6.0.

Orchestrates all trend scanning sources (Reddit, Google Trends, News)
for all brands. Called by the scan_and_generate cron job to discover
fresh content topics.

Scan sequence per brand:
1. Reddit: Hot and top posts from brand subreddits
2. Google Trends: Rising queries for brand keywords
3. News: Recent articles matching brand topics
4. Deduplicate across sources
5. Store unique high-relevance trends for content generation
"""

import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import structlog

from database.db import Database
from modules.trend_scanner.base_scanner import TrendItem
from modules.trend_scanner.reddit_scanner import RedditScanner
from modules.trend_scanner.google_trends_scanner import GoogleTrendsScanner
from modules.trend_scanner.news_scanner import NewsScanner
from modules.compliance.rate_limit_manager import RateLimitManager
from modules.infrastructure.resource_scheduler import get_scheduler

logger = structlog.get_logger(__name__)


class TrendScanner:
    """
    Coordinates trend scanning across all sources and brands.

    Manages the scan lifecycle: initializes scanners, iterates through
    brands, collects trends from all sources, deduplicates, and stores
    results. Respects resource constraints via ResourceScheduler.

    Attributes:
        SCAN_DELAY_BETWEEN_BRANDS: Seconds between brand scans.
        MIN_TRENDS_FOR_GENERATION: Minimum unused trends before triggering generation.
    """

    SCAN_DELAY_BETWEEN_BRANDS: float = 5.0
    MIN_TRENDS_FOR_GENERATION: int = 3

    def __init__(self) -> None:
        """
        Initializes the TrendScanner with all sub-scanners.

        Side effects:
            Creates instances of all scanner types.
            Creates Database and ResourceScheduler instances.
        """
        self.db = Database()
        self.reddit_scanner = RedditScanner()
        self.google_trends_scanner = GoogleTrendsScanner()
        self.news_scanner = NewsScanner()
        self._scheduler = get_scheduler()

    def scan_all_brands(self) -> dict:
        """
        Scans trends for all active brands.

        Returns:
            Dict with per-brand scan results and total counts.

        Side effects:
            Makes HTTP requests to Reddit, Google Trends, and News.
            Stores discovered trends in the database.
            Checks resource availability before scanning.
        """
        # Check if resources allow scanning
        can_start, reason = self._scheduler.can_start_job('trend_scanning')
        if not can_start:
            logger.warning("trend_scan_skipped_resources",
                            reason=reason)
            return {'status': 'skipped', 'reason': reason}

        from config.settings import load_brands_config
        brands = load_brands_config()

        results = {
            'status': 'completed',
            'brands_scanned': 0,
            'total_trends_found': 0,
            'total_trends_stored': 0,
            'per_brand': {},
        }

        for brand_id, brand_config in brands.items():
            # Check shutdown flag
            try:
                from modules.infrastructure.shutdown_handler import \
                    is_shutting_down
                if is_shutting_down():
                    logger.info("trend_scan_aborted_shutdown")
                    results['status'] = 'aborted'
                    break
            except ImportError:
                pass

            try:
                brand_result = self.scan_brand(brand_id, brand_config)
                results['per_brand'][brand_id] = brand_result
                results['brands_scanned'] += 1
                results['total_trends_found'] += brand_result.get(
                    'total_found', 0)
                results['total_trends_stored'] += brand_result.get(
                    'total_stored', 0)

                time.sleep(self.SCAN_DELAY_BETWEEN_BRANDS)

            except Exception as e:
                logger.error("brand_scan_failed",
                              brand_id=brand_id, error=str(e))
                results['per_brand'][brand_id] = {
                    'status': 'error',
                    'error': str(e),
                }

        logger.info("trend_scan_all_complete",
                      brands_scanned=results['brands_scanned'],
                      total_found=results['total_trends_found'],
                      total_stored=results['total_trends_stored'])

        return results

    def scan_brand(self, brand_id: str,
                   brand_config: dict) -> dict:
        """
        Scans all trend sources for a single brand.

        Parameters:
            brand_id: Brand identifier.
            brand_config: Full brand config from brands.json.

        Returns:
            Dict with per-source results and total counts.

        Side effects:
            Makes external API calls via sub-scanners.
            Stores trends in the database.
        """
        logger.info("brand_scan_started", brand_id=brand_id)
        start_time = time.time()

        result = {
            'status': 'completed',
            'reddit': {'found': 0, 'stored': 0},
            'google_trends': {'found': 0, 'stored': 0},
            'news': {'found': 0, 'stored': 0},
            'total_found': 0,
            'total_stored': 0,
            'duration_seconds': 0,
        }

        # Reddit scan
        try:
            reddit_trends = self.reddit_scanner.scan(brand_id, brand_config)
            reddit_stored = self.reddit_scanner.store_trends(
                brand_id, reddit_trends
            )
            result['reddit'] = {
                'found': len(reddit_trends),
                'stored': reddit_stored,
            }
        except Exception as e:
            logger.error("reddit_scan_failed",
                          brand_id=brand_id, error=str(e))
            result['reddit'] = {'found': 0, 'stored': 0, 'error': str(e)}

        # Google Trends scan
        try:
            gt_trends = self.google_trends_scanner.scan(
                brand_id, brand_config
            )
            gt_stored = self.google_trends_scanner.store_trends(
                brand_id, gt_trends
            )
            result['google_trends'] = {
                'found': len(gt_trends),
                'stored': gt_stored,
            }
        except Exception as e:
            logger.error("google_trends_scan_failed",
                          brand_id=brand_id, error=str(e))
            result['google_trends'] = {
                'found': 0, 'stored': 0, 'error': str(e)
            }

        # News scan
        try:
            news_trends = self.news_scanner.scan(brand_id, brand_config)
            news_stored = self.news_scanner.store_trends(
                brand_id, news_trends
            )
            result['news'] = {
                'found': len(news_trends),
                'stored': news_stored,
            }
        except Exception as e:
            logger.error("news_scan_failed",
                          brand_id=brand_id, error=str(e))
            result['news'] = {'found': 0, 'stored': 0, 'error': str(e)}

        # Totals
        result['total_found'] = sum(
            r.get('found', 0) for r in
            [result['reddit'], result['google_trends'], result['news']]
        )
        result['total_stored'] = sum(
            r.get('stored', 0) for r in
            [result['reddit'], result['google_trends'], result['news']]
        )
        result['duration_seconds'] = round(time.time() - start_time, 2)

        logger.info("brand_scan_complete",
                      brand_id=brand_id,
                      total_found=result['total_found'],
                      total_stored=result['total_stored'],
                      duration_seconds=result['duration_seconds'])

        return result

    def get_available_trends(self, brand_id: str,
                              limit: int = 10) -> list[dict]:
        """
        Gets unused trends for a brand, ordered by relevance.

        Parameters:
            brand_id: Brand identifier.
            limit: Maximum number of trends to return.

        Returns:
            List of trend dicts sorted by relevance score.

        Side effects:
            Reads from the trends table.
        """
        rows = self.db.fetch_all(
            "SELECT * FROM trends "
            "WHERE brand_id=? AND used=0 "
            "AND (expires_at IS NULL OR expires_at > ?) "
            "ORDER BY relevance_score DESC LIMIT ?",
            (brand_id, datetime.now(timezone.utc).isoformat(), limit)
        )
        return [dict(row) for row in rows]

    def brand_needs_trends(self, brand_id: str) -> bool:
        """
        Checks if a brand needs more trends for content generation.

        Parameters:
            brand_id: Brand identifier.

        Returns:
            True if unused trends are below the minimum threshold.

        Side effects:
            Queries the trends table.
        """
        available = self.get_available_trends(
            brand_id, self.MIN_TRENDS_FOR_GENERATION
        )
        return len(available) < self.MIN_TRENDS_FOR_GENERATION

    def consume_trend(self, trend_id: int) -> Optional[dict]:
        """
        Consumes a trend for content generation (marks as used).

        Parameters:
            trend_id: ID of the trend to consume.

        Returns:
            The trend dict, or None if not found.

        Side effects:
            Marks the trend as used in the database.
        """
        trend = self.db.fetch_one(
            "SELECT * FROM trends WHERE id=? AND used=0",
            (trend_id,)
        )

        if not trend:
            return None

        self.db.execute_write(
            "UPDATE trends SET used=1 WHERE id=?",
            (trend_id,)
        )

        logger.info("trend_consumed",
                      trend_id=trend_id,
                      brand_id=trend['brand_id'],
                      topic=trend['topic'])

        return dict(trend)

    def cleanup(self) -> dict:
        """
        Cleans up expired and old trends.

        Returns:
            Dict with cleanup statistics.

        Side effects:
            Deletes expired trends from the database.
        """
        now = datetime.now(timezone.utc).isoformat()

        # Delete expired trends
        self.db.execute_write(
            "DELETE FROM trends WHERE expires_at IS NOT NULL AND expires_at < ?",
            (now,)
        )

        # Delete old used trends (>30 days)
        from datetime import timedelta
        old_cutoff = (
            datetime.now(timezone.utc) - timedelta(days=30)
        ).isoformat()
        self.db.execute_write(
            "DELETE FROM trends WHERE used=1 AND discovered_at < ?",
            (old_cutoff,)
        )

        # Get remaining counts
        total = self.db.fetch_one(
            "SELECT COUNT(*) as cnt FROM trends"
        )
        unused = self.db.fetch_one(
            "SELECT COUNT(*) as cnt FROM trends WHERE used=0"
        )

        result = {
            'total_remaining': total['cnt'] if total else 0,
            'unused_remaining': unused['cnt'] if unused else 0,
        }

        logger.info("trend_cleanup_complete", **result)
        return result

    def get_scan_stats(self) -> dict:
        """
        Returns trend scanning statistics for monitoring.

        Returns:
            Dict with per-brand and per-source trend counts,
            total unused, and oldest/newest trend dates.

        Side effects:
            Multiple database queries.
        """
        # Per-brand unused counts
        brand_rows = self.db.fetch_all(
            "SELECT brand_id, COUNT(*) as count "
            "FROM trends WHERE used=0 "
            "AND (expires_at IS NULL OR expires_at > ?) "
            "GROUP BY brand_id",
            (datetime.now(timezone.utc).isoformat(),)
        )
        per_brand = {r['brand_id']: r['count'] for r in brand_rows}

        # Per-source counts
        source_rows = self.db.fetch_all(
            "SELECT source, COUNT(*) as count "
            "FROM trends WHERE used=0 GROUP BY source"
        )
        per_source = {r['source']: r['count'] for r in source_rows}

        # Total counts
        total = self.db.fetch_one(
            "SELECT COUNT(*) as total, "
            "MIN(discovered_at) as oldest, "
            "MAX(discovered_at) as newest "
            "FROM trends WHERE used=0"
        )

        return {
            'unused_per_brand': per_brand,
            'unused_per_source': per_source,
            'total_unused': total['total'] if total else 0,
            'oldest_trend': total['oldest'] if total else None,
            'newest_trend': total['newest'] if total else None,
        }
