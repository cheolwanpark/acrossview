"""Structured logging setup for the news crawler."""

import logging
import sys
from pathlib import Path

from .config import LOG_PATH, DATA_DIR


def setup_logger(name: str = "news_crawler", level: int = logging.INFO) -> logging.Logger:
    """Setup and return a configured logger.

    Args:
        name: Logger name
        level: Logging level (default INFO)

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Avoid duplicate handlers
    if logger.handlers:
        return logger

    # Create data directory if needed
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_format = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    )
    console_handler.setFormatter(console_format)
    logger.addHandler(console_handler)

    # File handler
    file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_format = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_format)
    logger.addHandler(file_handler)

    return logger


# Default logger instance
logger = setup_logger()
