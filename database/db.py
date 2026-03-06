"""
High-level database interface for AutoFarm Zero — Success Guru Network v6.0.

Provides a Database class with convenient methods for all common operations.
All database access should go through this module. Uses the DatabasePool
for connection management, WAL mode, and process-safe writes.
"""

import os
import logging
from datetime import datetime, timedelta
from typing import Any

from database.connection_pool import DatabasePool

logger = logging.getLogger(__name__)

# Global pool instance
_pool: DatabasePool | None = None


def get_pool() -> DatabasePool:
    """
    Returns the global DatabasePool singleton.

    Returns:
        DatabasePool instance, created on first call.

    Side effects:
        Creates the pool singleton on first invocation.
    """
    global _pool
    if _pool is None:
        _pool = DatabasePool()
    return _pool


class Database:
    """
    High-level database interface for AutoFarm Zero.

    Provides convenient methods for querying and modifying all tables.
    Wraps the DatabasePool with domain-specific operations for brands,
    scripts, videos, publishing, analytics, and system management.
    """

    def __init__(self, db_path: str = None):
        """
        Initializes the Database with a connection pool.

        Parameters:
            db_path: Optional path to SQLite database. Uses env var if not specified.
        """
        if db_path:
            self.pool = DatabasePool(db_path)
        else:
            self.pool = get_pool()

    def execute(self, sql: str, params: tuple = ()) -> Any:
        """
        Executes a SQL statement via the pool.

        Parameters:
            sql: SQL statement to execute.
            params: Parameters for parameterized queries.

        Returns:
            Cursor result from the execution.
        """
        return self.pool.execute(sql, params)

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        """
        Executes a query and returns results as list of dicts.

        Parameters:
            sql: SELECT statement.
            params: Query parameters.

        Returns:
            List of dictionaries, one per row.
        """
        return self.pool.query(sql, params)

    def query_one(self, sql: str, params: tuple = ()) -> dict | None:
        """
        Executes a query and returns a single result.

        Parameters:
            sql: SELECT statement.
            params: Query parameters.

        Returns:
            Dictionary for first row, or None.
        """
        return self.pool.query_one(sql, params)

    # Alias for compatibility — many modules use fetch_one
    fetch_one = query_one

    def fetch_all(self, sql: str, params: tuple = ()) -> list[dict]:
        """Alias for query() — used by some modules."""
        return self.query(sql, params)

    def write(self, sql: str, params: tuple = ()) -> Any:
        """
        Executes a write with process-level locking.

        Parameters:
            sql: INSERT/UPDATE/DELETE statement.
            params: Query parameters.

        Returns:
            Cursor result from the execution.
        """
        return self.pool.write_with_lock(sql, params)

    def insert(self, table: str, data: dict) -> int:
        """
        Inserts a row into a table and returns the new row ID.

        Parameters:
            table: Table name to insert into.
            data: Dictionary of column_name: value pairs.

        Returns:
            The rowid of the inserted row.
        """
        columns = ', '.join(data.keys())
        placeholders = ', '.join(['?'] * len(data))
        sql = f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"
        cursor = self.pool.write_with_lock(sql, tuple(data.values()))
        return cursor.lastrowid

    def update(self, table: str, data: dict, where: str, params: tuple = ()) -> int:
        """
        Updates rows in a table matching the where clause.

        Parameters:
            table: Table name.
            data: Dictionary of column_name: new_value pairs.
            where: WHERE clause (without 'WHERE' keyword).
            params: Parameters for the WHERE clause.

        Returns:
            Number of rows updated.
        """
        set_clause = ', '.join([f"{k} = ?" for k in data.keys()])
        sql = f"UPDATE {table} SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE {where}"
        all_params = tuple(data.values()) + params
        cursor = self.pool.write_with_lock(sql, all_params)
        return cursor.rowcount

    # === Brand Operations ===

    def get_brand(self, brand_id: str) -> dict | None:
        """
        Retrieves a brand record by ID.

        Parameters:
            brand_id: The brand identifier.

        Returns:
            Brand dict or None if not found.
        """
        return self.query_one("SELECT * FROM brands WHERE id = ?", (brand_id,))

    def get_all_brands(self, active_only: bool = True) -> list[dict]:
        """
        Retrieves all brand records.

        Parameters:
            active_only: If True, only returns active brands.

        Returns:
            List of brand dicts.
        """
        if active_only:
            return self.query("SELECT * FROM brands WHERE active = 1")
        return self.query("SELECT * FROM brands")

    # === Account Operations ===

    def get_account(self, brand_id: str, platform: str) -> dict | None:
        """
        Retrieves an account for a brand on a platform.

        Parameters:
            brand_id: The brand identifier.
            platform: Platform name.

        Returns:
            Account dict or None.
        """
        return self.query_one(
            "SELECT * FROM accounts WHERE brand_id = ? AND platform = ?",
            (brand_id, platform)
        )

    def get_accounts_by_brand(self, brand_id: str) -> list[dict]:
        """
        Retrieves all accounts for a brand.

        Parameters:
            brand_id: The brand identifier.

        Returns:
            List of account dicts.
        """
        return self.query(
            "SELECT * FROM accounts WHERE brand_id = ? ORDER BY platform",
            (brand_id,)
        )

    def get_active_accounts(self) -> list[dict]:
        """
        Retrieves all active accounts across all brands.

        Returns:
            List of account dicts with status 'active'.
        """
        return self.query("SELECT * FROM accounts WHERE status = 'active'")

    # === Trend Operations ===

    def save_trend(self, brand_id: str, source: str, topic: str,
                   raw_data: str = None, relevance_score: float = 0.0) -> int:
        """
        Saves a discovered trend.

        Parameters:
            brand_id: Brand this trend is relevant to.
            source: Source of the trend (reddit, newsapi, google_trends).
            topic: Trend topic text.
            raw_data: Raw JSON data from the source.
            relevance_score: How relevant this trend is (0.0-1.0).

        Returns:
            New trend row ID.
        """
        return self.insert('trends', {
            'brand_id': brand_id,
            'source': source,
            'topic': topic,
            'raw_data': raw_data,
            'relevance_score': relevance_score,
        })

    def get_unused_trends(self, brand_id: str, limit: int = 10) -> list[dict]:
        """
        Retrieves unused trends for a brand, ordered by relevance.

        Parameters:
            brand_id: Brand identifier.
            limit: Maximum number of trends to return.

        Returns:
            List of trend dicts.
        """
        return self.query(
            """SELECT * FROM trends
               WHERE brand_id = ? AND used = 0
               AND (expires_at IS NULL OR expires_at > datetime('now'))
               ORDER BY relevance_score DESC LIMIT ?""",
            (brand_id, limit)
        )

    # === Script Operations ===

    def save_script(self, brand_id: str, trend_id: int, hook: str, hook_type: str,
                    body: str, cta: str, script_text: str, word_count: int,
                    pillar: str = None, llm_provider: str = None,
                    llm_tokens_used: int = None) -> int:
        """
        Saves a generated script.

        Parameters:
            brand_id: Brand this script belongs to.
            trend_id: Source trend ID.
            hook: Opening hook text.
            hook_type: Type of hook used.
            body: Main script body.
            cta: Call to action text.
            script_text: Full combined script text.
            word_count: Total word count.
            pillar: Brand pillar this covers.
            llm_provider: Which LLM generated this.
            llm_tokens_used: Tokens consumed.

        Returns:
            New script row ID.
        """
        return self.insert('scripts', {
            'brand_id': brand_id,
            'trend_id': trend_id,
            'hook': hook,
            'hook_type': hook_type,
            'body': body,
            'cta': cta,
            'script_text': script_text,
            'word_count': word_count,
            'pillar': pillar,
            'llm_provider': llm_provider,
            'llm_tokens_used': llm_tokens_used,
            'status': 'draft',
        })

    def get_recent_scripts(self, brand_id: str, days: int = 30,
                           limit: int = 50) -> list[dict]:
        """
        Retrieves recent scripts for a brand.

        Parameters:
            brand_id: Brand identifier.
            days: Number of days to look back.
            limit: Maximum results.

        Returns:
            List of script dicts.
        """
        return self.query(
            """SELECT * FROM scripts
               WHERE brand_id = ? AND created_at > datetime('now', ?)
               ORDER BY created_at DESC LIMIT ?""",
            (brand_id, f'-{days} days', limit)
        )

    # === Video Operations ===

    def save_video(self, script_id: int, brand_id: str, video_path: str = None,
                   thumbnail_path: str = None, audio_path: str = None,
                   duration_seconds: float = None) -> int:
        """
        Saves a video record.

        Parameters:
            script_id: Associated script ID.
            brand_id: Brand identifier.
            video_path: Path to the assembled video file.
            thumbnail_path: Path to the thumbnail image.
            audio_path: Path to the TTS audio file.
            duration_seconds: Video duration in seconds.

        Returns:
            New video row ID.
        """
        return self.insert('videos', {
            'script_id': script_id,
            'brand_id': brand_id,
            'video_path': video_path,
            'thumbnail_path': thumbnail_path,
            'audio_path': audio_path,
            'duration_seconds': duration_seconds,
            'status': 'pending',
        })

    # === Review Operations ===

    def create_review(self, video_id: int, brand_id: str,
                      review_token: str, auto_approve_hours: int = 0) -> int:
        """
        Creates a review record for a video.

        Parameters:
            video_id: Video to review.
            brand_id: Brand identifier.
            review_token: Unique token for review URL.
            auto_approve_hours: Hours until auto-approval (0 = never).

        Returns:
            New review row ID.
        """
        data = {
            'video_id': video_id,
            'brand_id': brand_id,
            'review_token': review_token,
            'status': 'pending',
        }
        if auto_approve_hours > 0:
            data['auto_approve_at'] = (
                datetime.utcnow() + timedelta(hours=auto_approve_hours)
            ).isoformat()
        return self.insert('reviews', data)

    def get_pending_reviews(self) -> list[dict]:
        """
        Retrieves all pending reviews.

        Returns:
            List of review dicts with status 'pending'.
        """
        return self.query(
            """SELECT r.*, v.video_path, v.thumbnail_path, v.duration_seconds,
                      s.script_text, s.hook, s.hook_type, s.cta
               FROM reviews r
               JOIN videos v ON r.video_id = v.id
               JOIN scripts s ON v.script_id = s.id
               WHERE r.status = 'pending'
               ORDER BY r.created_at ASC"""
        )

    def approve_review(self, review_token: str, notes: str = None) -> bool:
        """
        Approves a review by token.

        Parameters:
            review_token: The unique review token.
            notes: Optional reviewer notes.

        Returns:
            True if a review was updated, False if token not found.
        """
        result = self.pool.write_with_lock(
            """UPDATE reviews SET status = 'approved', reviewer_notes = ?,
               reviewed_at = CURRENT_TIMESTAMP WHERE review_token = ? AND status = 'pending'""",
            (notes, review_token)
        )
        return result.rowcount > 0

    def reject_review(self, review_token: str, notes: str = None) -> bool:
        """
        Rejects a review by token.

        Parameters:
            review_token: The unique review token.
            notes: Optional rejection reason.

        Returns:
            True if a review was updated.
        """
        result = self.pool.write_with_lock(
            """UPDATE reviews SET status = 'rejected', reviewer_notes = ?,
               reviewed_at = CURRENT_TIMESTAMP WHERE review_token = ? AND status = 'pending'""",
            (notes, review_token)
        )
        return result.rowcount > 0

    # === Publish Job Operations ===

    def create_publish_job(self, video_id: int, brand_id: str, platform: str,
                           account_id: int, caption: str, hashtags: str,
                           scheduled_for: str, title: str = None,
                           description: str = None) -> int:
        """
        Creates a publish job record.

        Parameters:
            video_id: Video to publish.
            brand_id: Brand identifier.
            platform: Target platform.
            account_id: Account to publish from.
            caption: Post caption text.
            hashtags: Comma-separated hashtags.
            scheduled_for: ISO datetime for scheduled publishing.
            title: Title (for YouTube).
            description: Description (for YouTube).

        Returns:
            New publish job row ID.
        """
        return self.insert('publish_jobs', {
            'video_id': video_id,
            'brand_id': brand_id,
            'platform': platform,
            'account_id': account_id,
            'caption': caption,
            'hashtags': hashtags,
            'title': title,
            'description': description,
            'scheduled_for': scheduled_for,
            'status': 'pending',
        })

    def get_due_publish_jobs(self) -> list[dict]:
        """
        Retrieves publish jobs that are due for publishing.

        Returns:
            List of publish job dicts with scheduled_for <= now.
        """
        return self.query(
            """SELECT pj.*, v.video_path, v.thumbnail_path,
                      a.username, a.credentials_encrypted
               FROM publish_jobs pj
               JOIN videos v ON pj.video_id = v.id
               JOIN accounts a ON pj.account_id = a.id
               WHERE pj.status = 'pending'
               AND pj.scheduled_for <= datetime('now')
               ORDER BY pj.scheduled_for ASC"""
        )

    def mark_published(self, job_id: int, platform_post_id: str,
                       platform_url: str = None) -> None:
        """
        Marks a publish job as successfully published.

        Parameters:
            job_id: Publish job ID.
            platform_post_id: Post ID returned by the platform.
            platform_url: URL of the published post.
        """
        self.pool.write_with_lock(
            """UPDATE publish_jobs SET status = 'published',
               platform_post_id = ?, platform_url = ?,
               published_at = CURRENT_TIMESTAMP,
               updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (platform_post_id, platform_url, job_id)
        )

    def mark_publish_failed(self, job_id: int, error_message: str) -> None:
        """
        Marks a publish job as failed.

        Parameters:
            job_id: Publish job ID.
            error_message: Description of the failure.
        """
        self.pool.write_with_lock(
            """UPDATE publish_jobs SET status = 'failed',
               error_message = ?, retry_count = retry_count + 1,
               updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (error_message, job_id)
        )

    # === Analytics Operations ===

    def save_analytics(self, publish_job_id: int, brand_id: str, platform: str,
                       metrics: dict) -> int:
        """
        Saves analytics data for a published post.

        Parameters:
            publish_job_id: Associated publish job.
            brand_id: Brand identifier.
            platform: Platform name.
            metrics: Dictionary of metric_name: value pairs.

        Returns:
            New analytics row ID.
        """
        data = {
            'publish_job_id': publish_job_id,
            'brand_id': brand_id,
            'platform': platform,
        }
        data.update(metrics)
        return self.insert('analytics', data)

    # === Rate Limit Operations ===

    def get_rate_limit_count(self, brand_id: str, platform: str,
                             endpoint: str, window_type: str) -> dict | None:
        """
        Retrieves the current rate limit counter.

        Parameters:
            brand_id: Brand identifier.
            platform: Platform name.
            endpoint: API endpoint being tracked.
            window_type: 'hourly' or 'daily'.

        Returns:
            Rate limit record dict or None.
        """
        return self.query_one(
            """SELECT * FROM rate_limits
               WHERE brand_id = ? AND platform = ? AND endpoint = ? AND window_type = ?""",
            (brand_id, platform, endpoint, window_type)
        )

    def increment_rate_limit(self, brand_id: str, platform: str,
                              endpoint: str, window_type: str,
                              units: int = 1) -> None:
        """
        Increments a rate limit counter.

        Parameters:
            brand_id: Brand identifier.
            platform: Platform name.
            endpoint: API endpoint.
            window_type: 'hourly' or 'daily'.
            units: Number of units to add.
        """
        self.pool.write_with_lock(
            """INSERT INTO rate_limits (brand_id, platform, endpoint, window_type, count, units)
               VALUES (?, ?, ?, ?, 1, ?)
               ON CONFLICT(brand_id, platform, endpoint, window_type)
               DO UPDATE SET count = count + 1, units = units + ?""",
            (brand_id, platform, endpoint, window_type, units, units)
        )

    # === Job State Operations ===

    def save_job_state(self, job_id: int, job_type: str, brand_id: str,
                       state: str, previous_state: str = None,
                       error_message: str = None) -> int:
        """
        Saves or updates a job state record.

        Parameters:
            job_id: Content job ID.
            job_type: Type of job.
            brand_id: Brand identifier.
            state: New state value.
            previous_state: Previous state (for validation).
            error_message: Error details if failed.

        Returns:
            Job state row ID.
        """
        existing = self.query_one(
            "SELECT id FROM job_states WHERE job_id = ? AND job_type = ?",
            (job_id, job_type)
        )
        if existing:
            self.pool.write_with_lock(
                """UPDATE job_states SET state = ?, previous_state = ?,
                   error_message = ?, updated_at = CURRENT_TIMESTAMP
                   WHERE job_id = ? AND job_type = ?""",
                (state, previous_state, error_message, job_id, job_type)
            )
            return existing['id']
        else:
            return self.insert('job_states', {
                'job_id': job_id,
                'job_type': job_type,
                'brand_id': brand_id,
                'state': state,
                'previous_state': previous_state,
                'error_message': error_message,
            })

    # === LLM Request Logging ===

    def log_llm_request(self, provider: str, task_type: str, brand_id: str = None,
                        tokens_used: int = None, latency_ms: int = None,
                        success: bool = True, error_message: str = None) -> int:
        """
        Logs an LLM request for monitoring.

        Parameters:
            provider: LLM provider used ('ollama', 'groq', 'cached').
            task_type: Type of task performed.
            brand_id: Associated brand.
            tokens_used: Tokens consumed.
            latency_ms: Request latency in milliseconds.
            success: Whether the request succeeded.
            error_message: Error details if failed.

        Returns:
            New log row ID.
        """
        return self.insert('llm_requests', {
            'provider': provider,
            'task_type': task_type,
            'brand_id': brand_id,
            'tokens_used': tokens_used,
            'latency_ms': latency_ms,
            'success': 1 if success else 0,
            'error_message': error_message,
        })

    # === System Metrics ===

    def save_metric(self, metric_name: str, metric_value: float,
                    label: str = None) -> int:
        """
        Records a system metric data point.

        Parameters:
            metric_name: Name of the metric.
            metric_value: Numeric value.
            label: Optional label for grouping.

        Returns:
            New metric row ID.
        """
        return self.insert('system_metrics', {
            'metric_name': metric_name,
            'metric_value': metric_value,
            'label': label,
        })

    # === System Config ===

    def get_config(self, key: str, default: str = None) -> str | None:
        """
        Retrieves a system configuration value.

        Parameters:
            key: Configuration key.
            default: Default value if not found.

        Returns:
            Configuration value string, or default.
        """
        result = self.query_one(
            "SELECT value FROM system_config WHERE key = ?", (key,)
        )
        return result['value'] if result else default

    def set_config(self, key: str, value: str) -> None:
        """
        Sets a system configuration value.

        Parameters:
            key: Configuration key.
            value: Value to set.
        """
        self.pool.write_with_lock(
            """INSERT INTO system_config (key, value, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = CURRENT_TIMESTAMP""",
            (key, value, value)
        )

    # === Utility ===

    def get_last_publish_time(self, brand_id: str, platform: str) -> str | None:
        """
        Returns the last publish time for a brand on a platform.

        Parameters:
            brand_id: Brand identifier.
            platform: Platform name.

        Returns:
            ISO datetime string or None.
        """
        result = self.query_one(
            """SELECT published_at FROM publish_jobs
               WHERE brand_id = ? AND platform = ? AND status = 'published'
               ORDER BY published_at DESC LIMIT 1""",
            (brand_id, platform)
        )
        return result['published_at'] if result else None

    def count_publishes_today(self, brand_id: str, platform: str) -> int:
        """
        Counts how many posts were published today for a brand on a platform.

        Parameters:
            brand_id: Brand identifier.
            platform: Platform name.

        Returns:
            Number of publishes today.
        """
        result = self.query_one(
            """SELECT COUNT(*) as cnt FROM publish_jobs
               WHERE brand_id = ? AND platform = ? AND status = 'published'
               AND date(published_at) = date('now')""",
            (brand_id, platform)
        )
        return result['cnt'] if result else 0
