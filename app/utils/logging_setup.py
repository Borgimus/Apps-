"""
Logging configuration: rotating plain-text + rotating JSON (NDJSON) logs.

Call configure_logging() once at startup.  Every subsequent log call writes to:
  - stderr (console, human-readable)
  - logs/trading.log  (rotating, human-readable, 10 MB × 5)
  - logs/trading.jsonl (rotating, one JSON object per line, 20 MB × 10)

The JSON handler is useful for post-session analysis with jq / pandas.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
from datetime import datetime, timezone
from pathlib import Path


class _JsonFormatter(logging.Formatter):
    """Emit one JSON object per log record."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging(
    level: str = "INFO",
    log_dir: str | None = None,
) -> None:
    """
    Configure root logger.  Safe to call multiple times — handlers are only
    added once (idempotent).
    """
    try:
        from app.config import get_settings
        settings = get_settings()
        _log_dir = Path(log_dir or settings.log_file).parent
        _level_str = level or settings.log_level
    except Exception:
        _log_dir = Path("./logs")
        _level_str = level or "INFO"

    numeric = getattr(logging, _level_str.upper(), logging.INFO)
    _log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    if root.handlers:
        # Already configured — just adjust level
        root.setLevel(numeric)
        return

    root.setLevel(numeric)

    plain_fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    # ── Console ───────────────────────────────────────────────────────────────
    ch = logging.StreamHandler()
    ch.setLevel(numeric)
    ch.setFormatter(plain_fmt)
    root.addHandler(ch)

    # ── Plain rotating file ────────────────────────────────────────────────────
    fh = logging.handlers.RotatingFileHandler(
        _log_dir / "trading.log",
        maxBytes=10 * 1024 * 1024,   # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    fh.setLevel(numeric)
    fh.setFormatter(plain_fmt)
    root.addHandler(fh)

    # ── JSON rotating file ────────────────────────────────────────────────────
    jh = logging.handlers.RotatingFileHandler(
        _log_dir / "trading.jsonl",
        maxBytes=20 * 1024 * 1024,   # 20 MB
        backupCount=10,
        encoding="utf-8",
    )
    jh.setLevel(logging.DEBUG)
    jh.setFormatter(_JsonFormatter())
    root.addHandler(jh)

    # Suppress noisy third-party loggers
    for noisy in ("yfinance", "urllib3", "asyncio", "httpcore", "hpack", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.info(
        "Logging configured | level=%s | dir=%s", _level_str, _log_dir
    )


# Backwards-compat alias used by paper_trader.py and main.py
setup_logging = configure_logging
