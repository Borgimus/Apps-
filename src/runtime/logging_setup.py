"""Structured JSON logging with rotation and secret redaction.

Emits one JSON object per line (ingestable by any log pipeline). A redaction filter scrubs
secret-shaped substrings from every message BEFORE it is formatted, so API secrets, authorization
headers, and signed URLs never reach disk even if a caller accidentally passes them.
"""
from __future__ import annotations

import json
import logging
import re
from logging.handlers import RotatingFileHandler

_SECRET_PATTERNS = [
    re.compile(r"AK[A-Z0-9]{16,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{10,}", re.IGNORECASE),
    re.compile(r"(?i)(secret|api[_-]?key|password|token)\s*[=:]\s*\S+"),
    re.compile(r"https?://[^\s]*[?&](sig|signature|token)=[^\s&]+"),  # signed URLs
]

_STD_ATTRS = set(logging.makeLogRecord({}).__dict__.keys()) | {"message", "asctime"}


def scrub(text: str) -> str:
    for pat in _SECRET_PATTERNS:
        text = pat.sub("***REDACTED***", text)
    return text


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Scrub string args (the values that carry secrets). Only rewrite the msg template when
        # there are no args, so we never disturb %-format placeholders. The JsonFormatter also
        # scrubs the fully-merged message, so redaction holds either way.
        if record.args:
            record.args = tuple(scrub(a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.msg, str):
            record.msg = scrub(record.msg)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": scrub(record.getMessage()),
        }
        # Include any structured extras passed via logger.*(..., extra={...}).
        for k, v in record.__dict__.items():
            if k not in _STD_ATTRS and not k.startswith("_"):
                payload[k] = scrub(v) if isinstance(v, str) else v
        if record.exc_info:
            payload["exc"] = scrub(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


def configure_logging(*, level: str = "INFO", log_file: str | None = None,
                      max_bytes: int = 10_000_000, backups: int = 5) -> logging.Logger:
    """Configure the root logger for JSON output with optional rotating file handler."""
    root = logging.getLogger()
    root.setLevel(level.upper())
    for h in list(root.handlers):
        root.removeHandler(h)

    fmt = JsonFormatter()
    redactor = RedactionFilter()

    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    stream.addFilter(redactor)
    root.addHandler(stream)

    if log_file:
        fh = RotatingFileHandler(log_file, maxBytes=max_bytes, backupCount=backups)
        fh.setFormatter(fmt)
        fh.addFilter(redactor)
        root.addHandler(fh)

    return root
