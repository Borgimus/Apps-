"""
Structured logging configuration.

Uses structlog for machine-readable logs and rich for human-readable console output.
Every trade event, signal, rejection, fill, and error is logged with context.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path


def setup_logging(level: str | None = None):
    """Configure root logger with file rotation and console handler."""
    from app.config import get_settings
    settings = get_settings()

    log_level = level or settings.log_level
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    # Ensure log directory exists
    log_path = Path(settings.log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(numeric_level)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    # Console handler
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        ch = logging.StreamHandler()
        ch.setLevel(numeric_level)
        ch.setFormatter(fmt)
        root.addHandler(ch)

    # Rotating file handler
    fh = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
    )
    fh.setLevel(numeric_level)
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # Suppress noisy third-party loggers
    for noisy in ("yfinance", "urllib3", "asyncio", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.info("Logging initialised | level=%s | file=%s", log_level, log_path)
