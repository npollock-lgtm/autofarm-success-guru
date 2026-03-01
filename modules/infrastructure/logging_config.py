"""
Structured JSON logging configuration for AutoFarm Zero — Success Guru Network v6.0.

Configures structlog for all modules with JSON output. Every log entry includes:
timestamp, level, module, brand_id (if applicable), job_id (if applicable),
and duration_ms (for timed operations).

Usage:
    from modules.infrastructure.logging_config import configure_logging, get_logger

    configure_logging()
    logger = get_logger(__name__)
    logger.info("processing_video", brand_id="zen_success_guru", duration_ms=1250)
"""

import logging
import sys
import os
from pathlib import Path

import structlog


def configure_logging(log_level: str = None, log_to_file: bool = True) -> None:
    """
    Configures structured JSON logging for all modules.

    Sets up structlog with JSON rendering, timestamp injection, log level
    filtering, and optional file output. Should be called once at application
    startup before any logging occurs.

    Parameters:
        log_level: Logging level string ('DEBUG', 'INFO', 'WARNING', 'ERROR').
                   Defaults to LOG_LEVEL env var or 'INFO'.
        log_to_file: Whether to also write logs to the logs/ directory.

    Side effects:
        Configures the global structlog and stdlib logging settings.
        Creates logs/ directory if it doesn't exist.
    """
    level_str = log_level or os.getenv('LOG_LEVEL', 'INFO')
    level = getattr(logging, level_str.upper(), logging.INFO)

    # Configure stdlib logging (for libraries that use it)
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )

    # Set up file handler if requested
    if log_to_file:
        log_dir = Path(os.getenv('APP_DIR', '/app')) / 'logs'
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(str(log_dir / 'autofarm.log'))
        file_handler.setLevel(level)
        logging.getLogger().addHandler(file_handler)

    # Configure structlog
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        _add_module_info,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=shared_processors + [
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def _add_module_info(logger: object, method_name: str, event_dict: dict) -> dict:
    """
    Structlog processor that adds module name to log events.

    Parameters:
        logger: The logger instance.
        method_name: The log method name (info, warning, etc.).
        event_dict: The current log event dictionary.

    Returns:
        Updated event dict with 'module' key added.
    """
    if 'module' not in event_dict:
        # Try to get module from the structlog logger name
        record = event_dict.get('_record')
        if record:
            event_dict['module'] = record.name
    return event_dict


def get_logger(name: str = None) -> structlog.BoundLogger:
    """
    Returns a structured logger bound with the given name.

    Parameters:
        name: Logger name, typically __name__ of the calling module.

    Returns:
        A structlog BoundLogger instance configured for JSON output.
    """
    return structlog.get_logger(name or __name__)


def bind_context(**kwargs) -> None:
    """
    Binds context variables that will be included in all subsequent log entries
    within the current execution context (thread/coroutine).

    Useful for adding brand_id, job_id, etc. at the start of a job.

    Parameters:
        **kwargs: Key-value pairs to add to the logging context.

    Side effects:
        Updates the structlog context variables for the current thread.
    """
    structlog.contextvars.bind_contextvars(**kwargs)


def clear_context() -> None:
    """
    Clears all bound context variables.

    Should be called at the end of a job to prevent context leakage.

    Side effects:
        Removes all structlog context variables for the current thread.
    """
    structlog.contextvars.clear_contextvars()
