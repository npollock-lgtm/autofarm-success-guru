"""
Exponential backoff retry handler for AutoFarm Zero — Success Guru Network v6.0.

Provides a decorator and utility function for retrying operations with
exponential backoff and jitter. Sequence: 1s -> 2s -> 4s -> 8s -> 16s
(capped at max_delay), with +/-25% jitter to prevent thundering herd.

All external API calls should use this decorator for transient failure handling.
Works in conjunction with the CircuitBreaker for sustained failures.

Usage:
    @retry_with_backoff(max_retries=3, retry_on=(requests.Timeout, ConnectionError))
    def upload_video(session, video_path):
        ...
"""

import time
import random
import logging
from functools import wraps
from typing import Callable, Type

logger = logging.getLogger(__name__)


class RetryExhausted(Exception):
    """Raised when all retry attempts have been exhausted."""

    def __init__(self, func_name: str, attempts: int, last_error: Exception):
        """
        Parameters:
            func_name: Name of the function that failed.
            attempts: Total number of attempts made.
            last_error: The last exception encountered.
        """
        self.func_name = func_name
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(
            f"{func_name} failed after {attempts} attempts: {last_error}"
        )


def retry_with_backoff(
    max_retries: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    jitter: float = 0.25,
    retry_on: tuple[Type[Exception], ...] = (Exception,),
    on_retry: Callable = None,
):
    """
    Decorator for exponential backoff with jitter.

    Retries a function on specified exceptions with exponentially increasing
    delays. Jitter is applied to prevent synchronized retries across processes.

    Parameters:
        max_retries: Maximum number of retry attempts after the initial call.
        base_delay: Initial delay in seconds before first retry.
        max_delay: Maximum delay cap in seconds.
        jitter: Jitter factor (0.25 = +/-25% of computed delay).
        retry_on: Tuple of exception types to retry on.
        on_retry: Optional callback(func_name, attempt, exception, delay) on each retry.

    Returns:
        Decorated function with retry behaviour.

    Example:
        @retry_with_backoff(max_retries=3, retry_on=(ConnectionError,))
        def fetch_data():
            return requests.get(url, timeout=10)
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except retry_on as e:
                    last_exception = e
                    if attempt == max_retries:
                        logger.error(
                            f"{func.__name__} failed after {max_retries} retries: {e}",
                            extra={
                                'function': func.__name__,
                                'attempts': max_retries + 1,
                                'error': str(e),
                            }
                        )
                        raise

                    # Calculate delay with exponential backoff
                    delay = min(base_delay * (2 ** attempt), max_delay)
                    # Apply jitter
                    delay *= (1 + random.uniform(-jitter, jitter))
                    delay = max(0.1, delay)  # Minimum 100ms

                    logger.warning(
                        f"{func.__name__} attempt {attempt + 1}/{max_retries} "
                        f"failed: {e}. Retrying in {delay:.1f}s",
                        extra={
                            'function': func.__name__,
                            'attempt': attempt + 1,
                            'max_retries': max_retries,
                            'delay_seconds': round(delay, 1),
                            'error': str(e),
                        }
                    )

                    if on_retry:
                        try:
                            on_retry(func.__name__, attempt + 1, e, delay)
                        except Exception:
                            pass

                    time.sleep(delay)

            raise last_exception
        return wrapper
    return decorator


def retry_operation(
    operation: Callable,
    max_retries: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    jitter: float = 0.25,
    retry_on: tuple[Type[Exception], ...] = (Exception,),
    description: str = None,
):
    """
    Functional (non-decorator) version of retry with backoff.

    Useful when you need to retry a lambda or existing function without
    modifying its definition.

    Parameters:
        operation: Callable to retry.
        max_retries: Maximum retry attempts.
        base_delay: Initial delay seconds.
        max_delay: Maximum delay seconds.
        jitter: Jitter factor.
        retry_on: Exception types to retry on.
        description: Human-readable description for logging.

    Returns:
        Result of the successful operation call.

    Raises:
        RetryExhausted: If all retries are exhausted.
    """
    desc = description or getattr(operation, '__name__', 'operation')
    last_exception = None

    for attempt in range(max_retries + 1):
        try:
            return operation()
        except retry_on as e:
            last_exception = e
            if attempt == max_retries:
                raise RetryExhausted(desc, max_retries + 1, e)

            delay = min(base_delay * (2 ** attempt), max_delay)
            delay *= (1 + random.uniform(-jitter, jitter))
            delay = max(0.1, delay)

            logger.warning(
                f"{desc} attempt {attempt + 1}/{max_retries} failed: {e}. "
                f"Retrying in {delay:.1f}s"
            )
            time.sleep(delay)

    raise RetryExhausted(desc, max_retries + 1, last_exception)
