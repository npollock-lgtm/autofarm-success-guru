"""
Process-safe and thread-safe SQLite connection pool for AutoFarm Zero.

SQLite WAL mode allows concurrent readers but only one writer.
Multiple cron jobs may try to write simultaneously. This module provides:
- WAL mode enabled (concurrent reads)
- busy_timeout = 30000ms (30s wait for write lock)
- Process-level write lock using file locking
- WAL checkpoint management to prevent WAL file bloat
- Thread-local connections for safe multi-threaded access
"""

import sqlite3
import os
import sys
import threading
import logging
import time
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)


class DatabasePool:
    """
    Thread-safe and process-safe SQLite connection manager.

    Provides connection pooling with WAL mode, busy timeout handling,
    and process-level write locking for safe concurrent access from
    multiple cron jobs and application processes.
    """

    def __init__(self, db_path: str = None):
        """
        Initializes the database pool.

        Parameters:
            db_path: Path to the SQLite database file. Defaults to DATABASE_PATH env var.

        Side effects:
            Creates the lock file path alongside the database.
        """
        self.db_path = db_path or os.getenv('DATABASE_PATH', '/app/data/autofarm.db')
        self.lock_path = self.db_path + '.writelock'
        self._local = threading.local()
        self._checkpoint_counter = 0
        self._checkpoint_threshold = 1000

    def get_connection(self) -> sqlite3.Connection:
        """
        Returns a thread-local connection with WAL mode and appropriate timeouts.

        Returns:
            sqlite3.Connection configured with WAL mode, busy timeout, and Row factory.

        Side effects:
            Creates a new connection if one doesn't exist for the current thread.
            Enables WAL mode and sets performance pragmas on new connections.
        """
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            # Ensure directory exists
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

            conn = sqlite3.connect(self.db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
            conn.execute("PRAGMA foreign_keys=ON")
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return self._local.conn

    def close_connection(self) -> None:
        """
        Closes the thread-local connection if it exists.

        Side effects:
            Closes and removes the connection from thread-local storage.
        """
        if hasattr(self._local, 'conn') and self._local.conn is not None:
            try:
                self._local.conn.close()
            except Exception:
                pass
            self._local.conn = None

    @contextmanager
    def get_cursor(self):
        """
        Context manager that provides a cursor with automatic commit/rollback.

        Yields:
            sqlite3.Cursor for executing queries.

        Side effects:
            Commits on success, rolls back on exception.
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            yield cursor
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """
        Executes a SQL statement and returns the cursor.

        Parameters:
            sql: SQL statement to execute.
            params: Parameters for parameterized queries.

        Returns:
            sqlite3.Cursor after executing the statement.

        Side effects:
            Commits the transaction immediately.
        """
        conn = self.get_connection()
        cursor = conn.execute(sql, params)
        conn.commit()
        self._maybe_checkpoint()
        return cursor

    def executemany(self, sql: str, params_list: list) -> sqlite3.Cursor:
        """
        Executes a SQL statement with multiple parameter sets.

        Parameters:
            sql: SQL statement to execute.
            params_list: List of parameter tuples.

        Returns:
            sqlite3.Cursor after executing the batch.

        Side effects:
            Commits the transaction immediately.
        """
        conn = self.get_connection()
        cursor = conn.executemany(sql, params_list)
        conn.commit()
        return cursor

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        """
        Executes a query and returns results as list of dicts.

        Parameters:
            sql: SELECT statement to execute.
            params: Parameters for parameterized queries.

        Returns:
            List of dictionaries, one per row.
        """
        conn = self.get_connection()
        cursor = conn.execute(sql, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def query_one(self, sql: str, params: tuple = ()) -> dict | None:
        """
        Executes a query and returns a single result as dict.

        Parameters:
            sql: SELECT statement to execute.
            params: Parameters for parameterized queries.

        Returns:
            Dictionary for the first row, or None if no results.
        """
        conn = self.get_connection()
        cursor = conn.execute(sql, params)
        row = cursor.fetchone()
        return dict(row) if row else None

    def write_with_lock(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """
        Executes a write operation with process-level file lock.

        Uses file locking to ensure only one process writes at a time,
        preventing SQLITE_BUSY errors under heavy concurrent writes
        from multiple cron jobs.

        Parameters:
            sql: SQL write statement (INSERT/UPDATE/DELETE).
            params: Parameters for parameterized queries.

        Returns:
            sqlite3.Cursor after executing the statement.

        Side effects:
            Acquires and releases a process-level file lock.
            Commits the transaction.
        """
        conn = self.get_connection()

        # Use platform-appropriate file locking
        if sys.platform == 'win32':
            # Windows: use msvcrt for file locking
            return self._write_with_lock_windows(conn, sql, params)
        else:
            # Unix: use fcntl for file locking
            return self._write_with_lock_unix(conn, sql, params)

    def _write_with_lock_unix(self, conn: sqlite3.Connection, sql: str, params: tuple) -> sqlite3.Cursor:
        """
        Unix-specific write with fcntl file lock.

        Parameters:
            conn: Active database connection.
            sql: SQL write statement.
            params: Query parameters.

        Returns:
            sqlite3.Cursor after execution.
        """
        import fcntl
        lock_fd = open(self.lock_path, 'w')
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            cursor = conn.execute(sql, params)
            conn.commit()
            self._maybe_checkpoint()
            return cursor
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()

    def _write_with_lock_windows(self, conn: sqlite3.Connection, sql: str, params: tuple) -> sqlite3.Cursor:
        """
        Windows-specific write with msvcrt file lock.

        Parameters:
            conn: Active database connection.
            sql: SQL write statement.
            params: Query parameters.

        Returns:
            sqlite3.Cursor after execution.
        """
        import msvcrt
        lock_fd = open(self.lock_path, 'w')
        try:
            # Try to lock with retries
            for _ in range(30):
                try:
                    msvcrt.locking(lock_fd.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except IOError:
                    time.sleep(1)
            cursor = conn.execute(sql, params)
            conn.commit()
            self._maybe_checkpoint()
            return cursor
        finally:
            try:
                msvcrt.locking(lock_fd.fileno(), msvcrt.LK_UNLCK, 1)
            except Exception:
                pass
            lock_fd.close()

    def write_many_with_lock(self, sql: str, params_list: list) -> sqlite3.Cursor:
        """
        Executes multiple write operations with a single process-level lock.

        Parameters:
            sql: SQL write statement.
            params_list: List of parameter tuples.

        Returns:
            sqlite3.Cursor after execution.

        Side effects:
            Acquires and releases a process-level file lock.
        """
        conn = self.get_connection()

        if sys.platform == 'win32':
            import msvcrt
            lock_fd = open(self.lock_path, 'w')
            try:
                for _ in range(30):
                    try:
                        msvcrt.locking(lock_fd.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except IOError:
                        time.sleep(1)
                cursor = conn.executemany(sql, params_list)
                conn.commit()
                return cursor
            finally:
                try:
                    msvcrt.locking(lock_fd.fileno(), msvcrt.LK_UNLCK, 1)
                except Exception:
                    pass
                lock_fd.close()
        else:
            import fcntl
            lock_fd = open(self.lock_path, 'w')
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                cursor = conn.executemany(sql, params_list)
                conn.commit()
                return cursor
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                lock_fd.close()

    def checkpoint(self) -> None:
        """
        Runs WAL checkpoint to prevent unbounded WAL growth.

        Side effects:
            Executes PRAGMA wal_checkpoint(PASSIVE) to move WAL data
            into the main database file.
        """
        conn = self.get_connection()
        conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        logger.debug("WAL checkpoint completed")

    def _maybe_checkpoint(self) -> None:
        """
        Triggers a WAL checkpoint after threshold number of writes.

        Side effects:
            Increments write counter. Runs checkpoint if threshold exceeded.
        """
        self._checkpoint_counter += 1
        if self._checkpoint_counter >= self._checkpoint_threshold:
            self.checkpoint()
            self._checkpoint_counter = 0

    def init_schema(self, schema_path: str = None) -> None:
        """
        Initializes the database schema from schema.sql.

        Parameters:
            schema_path: Path to schema.sql file. Defaults to adjacent schema.sql.

        Side effects:
            Creates all tables, indexes, and default data.
        """
        if schema_path is None:
            schema_path = str(Path(__file__).parent / 'schema.sql')

        with open(schema_path, 'r') as f:
            schema_sql = f.read()

        conn = self.get_connection()
        conn.executescript(schema_sql)
        logger.info("Database schema initialized", extra={'schema_path': schema_path})

    def get_schema_version(self) -> str:
        """
        Returns the current schema version from system_config.

        Returns:
            Schema version string, or 'unknown' if not found.
        """
        try:
            result = self.query_one(
                "SELECT value FROM system_config WHERE key = 'schema_version'"
            )
            return result['value'] if result else 'unknown'
        except Exception:
            return 'unknown'
