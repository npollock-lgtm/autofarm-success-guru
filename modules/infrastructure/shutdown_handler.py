"""
Graceful shutdown handler for AutoFarm Zero — Success Guru Network v6.0.

Handles SIGTERM and SIGINT signals to ensure clean process termination.
Prevents half-assembled videos, mid-upload API calls, and database
corruption on shutdown.

Key behaviors:
- Sets a global shutdown flag that all long-running operations check
- Waits for current video assembly to finish (up to timeout)
- Commits any pending database transactions
- Runs WAL checkpoint before exit
- Cleans up temporary files from interrupted operations
- Logs shutdown reason and duration
"""

import os
import signal
import sys
import time
import threading
import logging
from pathlib import Path
from typing import Callable, Optional

import structlog

logger = structlog.get_logger(__name__)


class GracefulShutdownHandler:
    """
    Handles SIGTERM/SIGINT for graceful process shutdown.

    No half-assembled videos, no mid-upload API calls, no database corruption.
    Registers signal handlers on initialization and provides a shutdown flag
    that all long-running operations should check periodically.

    Attributes:
        SHUTDOWN_TIMEOUT: Maximum seconds to wait for operations to complete.
        shutdown_requested: Threading Event — set when shutdown signal received.
        shutdown_reason: String describing why shutdown was triggered.
    """

    SHUTDOWN_TIMEOUT: int = 120  # 2 minutes max wait

    # Singleton instance
    _instance: Optional['GracefulShutdownHandler'] = None

    def __new__(cls) -> 'GracefulShutdownHandler':
        """
        Ensures only one instance of shutdown handler exists (singleton).

        Returns:
            The singleton GracefulShutdownHandler instance.
        """
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        """
        Initializes the shutdown handler and registers signal handlers.

        Side effects:
            Registers SIGTERM and SIGINT signal handlers.
            Creates shutdown event and callback list.
            Only initializes once due to singleton pattern.
        """
        if self._initialized:
            return

        self.shutdown_requested: threading.Event = threading.Event()
        self.shutdown_reason: str = ""
        self._shutdown_callbacks: list[Callable] = []
        self._active_operations: dict[str, str] = {}
        self._lock: threading.Lock = threading.Lock()
        self._shutdown_start_time: Optional[float] = None

        # Register signal handlers
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        # On Windows, also handle SIGBREAK if available
        if hasattr(signal, 'SIGBREAK'):
            signal.signal(signal.SIGBREAK, self._handle_signal)

        self._initialized = True
        logger.info("shutdown_handler_initialized")

    def _handle_signal(self, signum: int, frame) -> None:
        """
        Signal handler for SIGTERM/SIGINT.

        Parameters:
            signum: Signal number received.
            frame: Current stack frame (unused).

        Side effects:
            Sets shutdown_requested event.
            Logs the signal received.
            Triggers shutdown callbacks.
            If called twice, forces immediate exit.
        """
        signal_name = signal.Signals(signum).name if hasattr(
            signal, 'Signals') else str(signum)

        if self.shutdown_requested.is_set():
            # Second signal — force exit
            logger.warning("forced_shutdown",
                            signal=signal_name,
                            msg="Received second shutdown signal, forcing exit")
            sys.exit(1)

        self.shutdown_reason = f"Signal {signal_name}"
        self._shutdown_start_time = time.time()
        self.shutdown_requested.set()

        logger.info("shutdown_requested",
                      signal=signal_name,
                      active_operations=len(self._active_operations))

        # Start shutdown sequence in a separate thread to avoid
        # blocking the signal handler
        shutdown_thread = threading.Thread(
            target=self._execute_shutdown,
            name="shutdown-sequence",
            daemon=True
        )
        shutdown_thread.start()

    def is_shutting_down(self) -> bool:
        """
        Check if shutdown has been requested.

        Returns:
            True if shutdown signal has been received.

        Side effects:
            None. Use this in tight loops and before starting new work.
        """
        return self.shutdown_requested.is_set()

    def register_callback(self, callback: Callable,
                          name: str = "") -> None:
        """
        Registers a callback to run during graceful shutdown.

        Parameters:
            callback: Function to call during shutdown (no args).
            name: Human-readable name for logging.

        Side effects:
            Adds callback to the shutdown sequence.
            Callbacks are executed in registration order.
        """
        self._shutdown_callbacks.append((callback, name or str(callback)))
        logger.debug("shutdown_callback_registered", name=name)

    def register_operation(self, operation_id: str,
                           description: str) -> None:
        """
        Registers an active operation that should complete before shutdown.

        Parameters:
            operation_id: Unique identifier for the operation.
            description: Human-readable description.

        Side effects:
            Adds to active operations tracking.
            Shutdown will wait for registered operations to complete.
        """
        with self._lock:
            self._active_operations[operation_id] = description
            logger.debug("operation_registered",
                          operation_id=operation_id,
                          description=description)

    def complete_operation(self, operation_id: str) -> None:
        """
        Marks an active operation as completed.

        Parameters:
            operation_id: Unique identifier for the operation.

        Side effects:
            Removes from active operations tracking.
        """
        with self._lock:
            self._active_operations.pop(operation_id, None)
            logger.debug("operation_completed", operation_id=operation_id)

    def wait_for_shutdown(self, timeout: Optional[float] = None) -> bool:
        """
        Blocks until shutdown is requested or timeout expires.

        Parameters:
            timeout: Maximum seconds to wait. None = wait forever.

        Returns:
            True if shutdown was requested, False if timeout expired.
        """
        return self.shutdown_requested.wait(timeout=timeout)

    def _execute_shutdown(self) -> None:
        """
        Executes the graceful shutdown sequence.

        Side effects:
            1. Waits for active operations to complete (up to timeout).
            2. Executes registered callbacks in order.
            3. Runs database cleanup (WAL checkpoint).
            4. Cleans up temporary files.
            5. Logs shutdown completion.
        """
        logger.info("shutdown_sequence_started",
                      reason=self.shutdown_reason,
                      active_operations=dict(self._active_operations))

        # Phase 1: Wait for active operations
        self._wait_for_operations()

        # Phase 2: Execute registered callbacks
        self._execute_callbacks()

        # Phase 3: Database cleanup
        self._cleanup_database()

        # Phase 4: Clean temporary files
        self._cleanup_temp_files()

        # Phase 5: Final logging
        duration = time.time() - self._shutdown_start_time
        logger.info("shutdown_complete",
                      reason=self.shutdown_reason,
                      duration_seconds=round(duration, 2),
                      remaining_operations=len(self._active_operations))

    def _wait_for_operations(self) -> None:
        """
        Waits for all active operations to complete or timeout.

        Side effects:
            Blocks until operations complete or SHUTDOWN_TIMEOUT reached.
            Logs periodic updates on remaining operations.
        """
        if not self._active_operations:
            return

        start = time.time()
        check_interval = 5  # seconds

        while self._active_operations and \
                (time.time() - start) < self.SHUTDOWN_TIMEOUT:
            with self._lock:
                remaining = dict(self._active_operations)

            logger.info("waiting_for_operations",
                          remaining=len(remaining),
                          operations=remaining,
                          elapsed_seconds=round(time.time() - start, 1))

            time.sleep(check_interval)

        if self._active_operations:
            logger.warning("operations_timeout",
                            remaining=dict(self._active_operations),
                            timeout_seconds=self.SHUTDOWN_TIMEOUT)

    def _execute_callbacks(self) -> None:
        """
        Executes all registered shutdown callbacks in order.

        Side effects:
            Calls each registered callback function.
            Logs failures but continues with remaining callbacks.
        """
        for callback, name in self._shutdown_callbacks:
            try:
                logger.info("executing_shutdown_callback", name=name)
                callback()
            except Exception as e:
                logger.error("shutdown_callback_failed",
                              name=name, error=str(e))

    def _cleanup_database(self) -> None:
        """
        Performs database cleanup before shutdown.

        Side effects:
            Runs WAL checkpoint to flush writes.
            Closes all database connections.
        """
        try:
            from database.connection_pool import DatabasePool
            pool = DatabasePool()
            pool.checkpoint()
            pool.close()
            logger.info("database_cleanup_complete")
        except Exception as e:
            logger.error("database_cleanup_failed", error=str(e))

    def _cleanup_temp_files(self) -> None:
        """
        Removes temporary files from interrupted operations.

        Side effects:
            Deletes files matching temp patterns in media directories.
        """
        try:
            temp_patterns = ['*_temp.*', '*_partial.*', '*.tmp',
                             '*_preview.mp4']
            media_dir = Path(os.getenv('MEDIA_DIR', '/app/media'))

            if not media_dir.exists():
                return

            cleaned = 0
            for pattern in temp_patterns:
                for temp_file in media_dir.rglob(pattern):
                    try:
                        temp_file.unlink()
                        cleaned += 1
                    except OSError:
                        pass

            if cleaned > 0:
                logger.info("temp_files_cleaned", count=cleaned)

        except Exception as e:
            logger.warning("temp_cleanup_failed", error=str(e))


# Module-level convenience functions
_handler: Optional[GracefulShutdownHandler] = None


def init_shutdown_handler() -> GracefulShutdownHandler:
    """
    Initializes and returns the global shutdown handler.

    Returns:
        The singleton GracefulShutdownHandler instance.

    Side effects:
        Creates the handler and registers signal handlers.
    """
    global _handler
    _handler = GracefulShutdownHandler()
    return _handler


def is_shutting_down() -> bool:
    """
    Checks if shutdown has been requested.

    Returns:
        True if shutdown signal received, False if handler not initialized.
    """
    if _handler is None:
        return False
    return _handler.is_shutting_down()


def register_shutdown_callback(callback: Callable,
                                name: str = "") -> None:
    """
    Registers a callback for the shutdown sequence.

    Parameters:
        callback: Function to call during shutdown.
        name: Human-readable name for logging.

    Side effects:
        Adds callback to the global shutdown handler.
    """
    if _handler is not None:
        _handler.register_callback(callback, name)
