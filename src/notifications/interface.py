"""Provider-neutral notifications.

Defines the event catalog, a Notifier interface, and a secret-redaction helper. Concrete
providers (console, webhook, email, …) implement ``Notifier``. Credentials are NEVER embedded
in notification payloads or logs — the redactor strips known secret-shaped fields.
"""
from __future__ import annotations

import abc
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class Event(str, Enum):
    IMPORT_ACCEPTED = "IMPORT_ACCEPTED"
    IMPORT_REJECTED = "IMPORT_REJECTED"
    SETUP_QUALIFIED = "SETUP_QUALIFIED"
    ENTRY_SUBMITTED = "ENTRY_SUBMITTED"
    ENTRY_FILLED = "ENTRY_FILLED"
    ENTRY_CANCELED = "ENTRY_CANCELED"
    ENTRY_REJECTED = "ENTRY_REJECTED"
    STOP_MISSING = "STOP_MISSING"
    STOP_RECOVERED = "STOP_RECOVERED"
    PARTIAL_5R_SUBMITTED = "PARTIAL_5R_SUBMITTED"
    PARTIAL_5R_FILLED = "PARTIAL_5R_FILLED"
    STOP_MOVED_BREAKEVEN = "STOP_MOVED_BREAKEVEN"
    DAILY_CLOSE_EXIT_TRIGGERED = "DAILY_CLOSE_EXIT_TRIGGERED"
    DAILY_CLOSE_EXIT_FILLED = "DAILY_CLOSE_EXIT_FILLED"
    BROKER_DISCONNECT = "BROKER_DISCONNECT"
    DATA_DISCONNECT = "DATA_DISCONNECT"
    RECON_MISMATCH = "RECON_MISMATCH"
    PROCESS_START = "PROCESS_START"
    PROCESS_RESTART = "PROCESS_RESTART"
    PROCESS_SHUTDOWN = "PROCESS_SHUTDOWN"


class Severity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


# Fields whose values must never leave the process in a notification.
_SECRET_KEY_RE = re.compile(
    r"(secret|api[_-]?key|authorization|password|token|access[_-]?key)", re.IGNORECASE
)
_SECRET_VALUE_RE = [
    re.compile(r"AK[A-Z0-9]{16,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{10,}", re.IGNORECASE),
]


def redact(data: dict) -> dict:
    """Return a copy with secret-shaped keys masked and secret-shaped values scrubbed."""
    out: dict = {}
    for k, v in data.items():
        if _SECRET_KEY_RE.search(str(k)):
            out[k] = "***REDACTED***"
            continue
        if isinstance(v, dict):
            out[k] = redact(v)
        elif isinstance(v, str):
            masked = v
            for pat in _SECRET_VALUE_RE:
                masked = pat.sub("***REDACTED***", masked)
            out[k] = masked
        else:
            out[k] = v
    return out


@dataclass
class Notification:
    event: Event
    severity: Severity
    title: str
    body: str
    data: dict = field(default_factory=dict)
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def safe_payload(self) -> dict:
        """JSON-able payload with secrets redacted — the only form ever sent/logged."""
        return {
            "event": self.event.value,
            "severity": self.severity.value,
            "title": self.title,
            "body": self.body,
            "data": redact(self.data),
            "at": self.at.isoformat(),
        }


class Notifier(abc.ABC):
    @abc.abstractmethod
    def send(self, notification: Notification) -> None: ...
