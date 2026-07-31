"""Concrete notifiers and a fan-out dispatcher.

- InMemoryNotifier: records notifications (tests / audit).
- ConsoleNotifier: prints the redacted payload (structured, secret-free).
- WebhookNotifier: posts the redacted payload via an INJECTED sender (provider-neutral; the
  URL/credentials come from the environment and are never committed or logged).
- NotificationDispatcher: fans out to multiple notifiers; one failing provider never blocks
  the others or the trading loop.
"""
from __future__ import annotations

import json
from typing import Callable

from .interface import Notification, Notifier


class InMemoryNotifier(Notifier):
    def __init__(self):
        self.sent: list[Notification] = []

    def send(self, notification: Notification) -> None:
        self.sent.append(notification)

    def events(self) -> list[str]:
        return [n.event.value for n in self.sent]


class ConsoleNotifier(Notifier):
    def __init__(self, printer: Callable[[str], None] = print):
        self._print = printer

    def send(self, notification: Notification) -> None:
        self._print(json.dumps(notification.safe_payload()))


class WebhookNotifier(Notifier):
    """Provider-neutral webhook. ``sender(url, json_payload)`` is injected so the transport
    (httpx/requests/etc.) and credentials stay out of this module and out of tests."""

    def __init__(self, url: str, sender: Callable[[str, dict], None]):
        if not url:
            raise ValueError("webhook url required (from env; never committed)")
        self._url = url
        self._sender = sender

    def send(self, notification: Notification) -> None:
        self._sender(self._url, notification.safe_payload())


class NotificationDispatcher(Notifier):
    def __init__(self, notifiers: list[Notifier], on_error: Callable[[Notifier, Exception], None] | None = None):
        self._notifiers = notifiers
        self._on_error = on_error

    def send(self, notification: Notification) -> None:
        for n in self._notifiers:
            try:
                n.send(notification)
            except Exception as exc:  # noqa: BLE001 — one bad provider must not break the loop
                if self._on_error:
                    self._on_error(n, exc)
