"""Logging configuration for human-readable and machine-readable output.

Call ``configure_logging`` once at startup. Runtime launchers often redirect
stderr to ``logs/session_YYYY-MM-DD.log``. This module detects that case and
does not attach a second file handler to the same path, preventing every log
record from being written twice.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO


class _JsonFormatter(logging.Formatter):
    """Emit one compact JSON object per log record."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(
                record.created,
                tz=timezone.utc,
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload)


class _NameFilter(logging.Filter):
    """Pass only records whose logger name starts with ``prefix``."""

    def __init__(self, prefix: str):
        super().__init__()
        self._prefix = prefix

    def filter(self, record: logging.LogRecord) -> bool:
        return record.name.startswith(self._prefix)


def _stream_targets_file(stream: TextIO, path: Path) -> bool:
    """Return True when a process stream is already redirected to ``path``."""
    try:
        fd = stream.fileno()
        proc_fd = Path(f"/proc/self/fd/{fd}")
        if proc_fd.exists() and path.exists():
            return os.path.samefile(proc_fd, path)
    except (AttributeError, OSError, ValueError):
        return False
    return False


def _session_log_is_redirect_target(path: Path) -> bool:
    env_override = os.getenv("SESSION_LOG_REDIRECTED", "").strip().lower()
    if env_override in {"1", "true", "yes", "on"}:
        return True
    return _stream_targets_file(sys.stderr, path) or _stream_targets_file(
        sys.stdout,
        path,
    )


def configure_logging(
    level: str = "INFO",
    log_dir: str | None = None,
) -> None:
    """Configure the root logger once with rotating output handlers."""
    try:
        from app.config import get_settings

        settings = get_settings()
        configured_log = Path(log_dir or settings.log_file)
        log_path = (
            configured_log
            if configured_log.suffix
            else configured_log / "trading.log"
        )
        resolved_log_dir = log_path.parent
        level_string = level or settings.log_level
    except Exception:
        resolved_log_dir = Path(log_dir or "./logs")
        level_string = level or "INFO"

    numeric = getattr(
        logging,
        level_string.upper(),
        logging.INFO,
    )
    resolved_log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    if root.handlers:
        root.setLevel(min(root.level, numeric))
        return

    root.setLevel(logging.DEBUG)

    plain_format = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    console = logging.StreamHandler()
    console.setLevel(numeric)
    console.setFormatter(plain_format)
    root.addHandler(console)

    trading_file = logging.handlers.RotatingFileHandler(
        resolved_log_dir / "trading.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    trading_file.setLevel(numeric)
    trading_file.setFormatter(plain_format)
    root.addHandler(trading_file)

    json_file = logging.handlers.RotatingFileHandler(
        resolved_log_dir / "trading.jsonl",
        maxBytes=20 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )
    json_file.setLevel(logging.DEBUG)
    json_file.setFormatter(_JsonFormatter())
    root.addHandler(json_file)

    errors_file = logging.handlers.RotatingFileHandler(
        resolved_log_dir / "errors.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )
    errors_file.setLevel(logging.ERROR)
    errors_file.setFormatter(plain_format)
    root.addHandler(errors_file)

    broker_file = logging.handlers.RotatingFileHandler(
        resolved_log_dir / "broker.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    broker_file.setLevel(logging.DEBUG)
    broker_file.setFormatter(plain_format)
    broker_file.addFilter(_NameFilter("app.brokers"))
    root.addHandler(broker_file)

    api_file = logging.handlers.RotatingFileHandler(
        resolved_log_dir / "api.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    api_file.setLevel(logging.DEBUG)
    api_file.setFormatter(plain_format)
    api_file.addFilter(_NameFilter("app.api"))
    root.addHandler(api_file)

    today = datetime.now().strftime("%Y-%m-%d")
    session_path = resolved_log_dir / f"session_{today}.log"
    session_redirected = _session_log_is_redirect_target(session_path)
    if not session_redirected:
        daily_file = logging.handlers.TimedRotatingFileHandler(
            session_path,
            when="midnight",
            backupCount=30,
            encoding="utf-8",
        )
        daily_file.setLevel(numeric)
        daily_file.setFormatter(plain_format)
        root.addHandler(daily_file)

    for noisy in (
        "yfinance",
        "urllib3",
        "asyncio",
        "httpcore",
        "hpack",
        "httpx",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.info(
        "Logging configured | level=%s | dir=%s | handlers=%d | "
        "session_redirected=%s",
        level_string,
        resolved_log_dir,
        len(root.handlers),
        session_redirected,
    )


setup_logging = configure_logging
