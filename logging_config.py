"""
Centralized logging configuration for TestAgent Tool
"""

import logging
import logging.config
from datetime import datetime
from pathlib import Path
from typing import Optional

import pytz

# Import LOG_LEVEL and TIMEZONE from settings
try:
    from config import LOG_LEVEL, TIMEZONE
except ImportError:
    # Fallback if settings not available
    LOG_LEVEL = "INFO"
    TIMEZONE = "UTC"


class ISOFormatter(logging.Formatter):
    """Custom formatter for ISO 8601 datetime with timezone and microseconds."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Get timezone from environment, fallback to UTC
        try:
            self.timezone = pytz.timezone(TIMEZONE)
        except pytz.UnknownTimeZoneError:
            print(f"Warning: Unknown timezone '{TIMEZONE}'. Using UTC.")
            self.timezone = pytz.UTC

    def formatTime(self, record, datefmt=None):
        """Format time as ISO 8601 with timezone offset and microseconds."""
        # Create datetime from timestamp and localize to the configured timezone
        dt_utc = datetime.fromtimestamp(record.created, tz=pytz.UTC)
        dt_local = dt_utc.astimezone(self.timezone)

        # Return ISO format with timezone
        return dt_local.isoformat()


# Default logging configuration
def get_logging_config(log_level: str = None) -> dict:
    """
    Get logging configuration with dynamic log level.

    Args:
        log_level: Log level to use. If None, uses environment LOG_LEVEL.

    Returns:
        Logging configuration dictionary
    """
    if log_level is None:
        log_level = LOG_LEVEL

    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "detailed": {
                "format": (
                    "[%(asctime)s] %(name)s - %(levelname)s - "
                    "%(filename)s:%(lineno)d - %(message)s"
                ),
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
            "simple": {"format": "%(levelname)s - %(name)s - %(message)s"},
            "api": {
                "format": "[%(asctime)s] API - %(levelname)s - %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
            "agent": {
                "()": ISOFormatter,
                "format": "%(asctime)s - AGENT - %(levelname)s - %(message)s",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "level": log_level,
                "formatter": "detailed",
                "stream": "ext://sys.stdout",
            },
            "file_general": {
                "class": "logging.handlers.RotatingFileHandler",
                "level": log_level,
                "formatter": "detailed",
                "filename": "logs/testagent.log",
                "maxBytes": 10485760,  # 10MB
                "backupCount": 5,
                "encoding": "utf-8",
            },
            "file_api": {
                "class": "logging.handlers.RotatingFileHandler",
                "level": log_level,
                "formatter": "api",
                "filename": "logs/api.log",
                "maxBytes": 5242880,  # 5MB
                "backupCount": 3,
                "encoding": "utf-8",
            },
            "file_agent": {
                "class": "logging.handlers.RotatingFileHandler",
                "level": log_level,
                "formatter": "agent",
                "filename": "logs/agent.log",
                "maxBytes": 5242880,  # 5MB
                "backupCount": 3,
                "encoding": "utf-8",
            },
            "file_errors": {
                "class": "logging.handlers.RotatingFileHandler",
                "level": "ERROR",
                "formatter": "detailed",
                "filename": "logs/errors.log",
                "maxBytes": 5242880,  # 5MB
                "backupCount": 10,
                "encoding": "utf-8",
            },
        },
        "loggers": {
            "testagent": {
                "level": log_level,
                "handlers": ["console", "file_general", "file_errors"],
                "propagate": False,
            },
            "testagent.auth": {
                "level": log_level,
                "handlers": ["console", "file_general", "file_errors"],
                "propagate": False,
            },
            "testagent.api": {
                "level": log_level,
                "handlers": ["console", "file_api", "file_errors"],
                "propagate": False,
            },
            "testagent.agent": {
                "level": log_level,
                "handlers": ["console", "file_agent", "file_errors"],
                "propagate": False,
            },
            "testagent.database": {
                "level": log_level,
                "handlers": ["console", "file_general", "file_errors"],
                "propagate": False,
            },
            "testagent.ui": {
                "level": log_level,
                "handlers": ["console", "file_general", "file_errors"],
                "propagate": False,
            },
            "testagent.session": {
                "level": log_level,
                "handlers": ["console", "file_general", "file_errors"],
                "propagate": False,
            },
        },
        "root": {"level": log_level, "handlers": ["console"]},
    }


def setup_logging(
    config_dict: Optional[dict] = None, logs_dir: str = "logs", log_level: str = None
) -> None:
    """
    Set up logging configuration for the application.

    Args:
        config_dict: Custom logging configuration dictionary. If None, uses default.
        logs_dir: Directory to store log files. Defaults to "logs".
        log_level: Override log level. If None, uses environment LOG_LEVEL.
    """
    # Create logs directory if it doesn't exist
    log_path = Path(logs_dir)
    log_path.mkdir(exist_ok=True)

    # Use provided config or generate default with dynamic log level
    config = config_dict or get_logging_config(log_level)

    # Update file paths to use the specified logs directory
    for handler_config in config.get("handlers", {}).values():
        if "filename" in handler_config:
            handler_config["filename"] = str(
                log_path / Path(handler_config["filename"]).name
            )

    # Apply logging configuration
    logging.config.dictConfig(config)


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance with the specified name.

    Args:
        name: Logger name (should start with 'testagent.' for proper configuration)

    Returns:
        Configured logger instance
    """
    return logging.getLogger(name)


def log_exception(
    logger: logging.Logger, exception: Exception, context: str = ""
) -> None:
    """
    Log an exception with full traceback.

    Args:
        logger: Logger instance to use
        exception: Exception to log
        context: Additional context information
    """
    if context:
        logger.exception(f"{context}: {str(exception)}")
    else:
        logger.exception(f"Exception occurred: {str(exception)}")


def set_log_level(logger_name: str, level: str) -> None:
    """
    Dynamically change log level for a specific logger.

    Args:
        logger_name: Name of the logger
        level: New log level ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')
    """
    logger = logging.getLogger(logger_name)
    numeric_level = getattr(logging, level.upper(), None)
    if not isinstance(numeric_level, int):
        raise ValueError(f"Invalid log level: {level}")
    logger.setLevel(numeric_level)


def get_current_log_level() -> str:
    """
    Get the current log level from environment configuration.

    Returns:
        Current log level string
    """
    return LOG_LEVEL


def setup_logging_with_env() -> None:
    """
    Convenience function to set up logging using environment variables.
    This is the recommended way to initialize logging in the application.
    """
    setup_logging(log_level=LOG_LEVEL)


# Logger instances for common use
def get_auth_logger() -> logging.Logger:
    """Get logger for authentication operations."""
    return get_logger("testagent.auth")


def get_api_logger() -> logging.Logger:
    """Get logger for API operations."""
    return get_logger("testagent.api")


def get_agent_logger() -> logging.Logger:
    """Get logger for agent operations."""
    return get_logger("testagent.agent")


def get_database_logger() -> logging.Logger:
    """Get logger for database operations."""
    return get_logger("testagent.database")


def get_ui_logger() -> logging.Logger:
    """Get logger for UI operations."""
    return get_logger("testagent.ui")


def get_session_logger() -> logging.Logger:
    """Get logger for session operations."""
    return get_logger("testagent.session")
