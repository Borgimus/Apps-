"""
Logging configuration: rotating plain-text, rotating JSON, per-component
files, and a daily session log.

Call configure_logging() once at startup.  Every subsequent log call writes to:
  logs/trading.log          — all events, rotating (10 MB × 5)
  logs/trading.jsonl        — all events as NDJSON, rotating (20 MB × 10)
  logs/errors.log           — ERROR and above only, rotating (5 MB × 10)
  logs/broker.log           — app.brokers.* events, rotating (5 MB × 5)
  logs/api.log              — app.api.* events, rotating (5 MB × 5)
  logs/session_YYYY-MM-DD.log — today's session in plain text (daily rotation)

The JSON handler is useful for post-session analysis with jq / pandas.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
from datetime import datetime, timezone
from pathlib import Path


class _JsonFormatter(logging.Formatter):
    """Emit one compact JSON object per log record."""

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


class _NameFilter(logging.Filter):
    """Pass only records whose logger name starts with `prefix`."""

    def __init__(self, prefix: str):
        super().__init__()
        self._prefix = prefix

    def filter(self, record: logging.LogRecord) -> bool:
        return record.name.startswith(self._prefix)


def configure_logging(
    level: str = "INFO",
    log_dir: str | None = None,
) -> None:
    """
    Configure the root logger with all handlers.

    Safe to call multiple times — handlers are added only once (idempotent).
    The `level` parameter controls the console and the main trading.log;
    errors.log always captures ERROR+, broker.log and api.log capture DEBUG+
    for their respective namespaces.
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
        root.setLevel(min(root.level, numeric))
        return

    root.setLevel(logging.DEBUG)   # root accepts everything; handlers filter

    plain_fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    # ── Console (human-readable, user-specified level) ────────────────────────
    ch = logging.StreamHandler()
    ch.setLevel(numeric)
    ch.setFormatter(plain_fmt)
    root.addHandler(ch)

    # ── Main rotating plain-text (all events) ─────────────────────────────────
    fh = logging.handlers.RotatingFileHandler(
        _log_dir / "trading.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    fh.setLevel(numeric)
    fh.setFormatter(plain_fmt)
    root.addHandler(fh)

    # ── Main rotating JSON (all events, for machine consumption) ──────────────
    jh = logging.handlers.RotatingFileHandler(
        _log_dir / "trading.jsonl",
        maxBytes=20 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )
    jh.setLevel(logging.DEBUG)
    jh.setFormatter(_JsonFormatter())
    root.addHandler(jh)

    # ── Errors-only rotating file ─────────────────────────────────────────────
    eh = logging.handlers.RotatingFileHandler(
        _log_dir / "errors.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )
    eh.setLevel(logging.ERROR)
    eh.setFormatter(plain_fmt)
    root.addHandler(eh)

    # ── Broker events (app.brokers.*) ─────────────────────────────────────────
    bh = logging.handlers.RotatingFileHandler(
        _log_dir / "broker.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    bh.setLevel(logging.DEBUG)
    bh.setFormatter(plain_fmt)
    bh.addFilter(_NameFilter("app.brokers"))
    root.addHandler(bh)

    # ── API events (app.api.*) ────────────────────────────────────────────────
    ah = logging.handlers.RotatingFileHandler(
        _log_dir / "api.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    ah.setLevel(logging.DEBUG)
    ah.setFormatter(plain_fmt)
    ah.addFilter(_NameFilter("app.api"))
    root.addHandler(ah)

    # ── Daily session log (rotates at midnight) ────────────────────────────────
    today = datetime.now().strftime("%Y-%m-%d")
    dh = logging.handlers.TimedRotatingFileHandler(
        _log_dir / f"session_{today}.log",
        when="midnight",
        backupCount=30,
        encoding="utf-8",
    )
    dh.setLevel(numeric)
    dh.setFormatter(plain_fmt)
    root.addHandler(dh)

    # Suppress noisy third-party loggers
    for noisy in ("yfinance", "urllib3", "asyncio", "httpcore", "hpack", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.info(
        "Logging configured | level=%s | dir=%s | handlers=%d",
        _level_str, _log_dir, len(root.handlers),
    )


# Backwards-compat alias used by paper_trader.py and main.py
setup_logging = configure_logging
