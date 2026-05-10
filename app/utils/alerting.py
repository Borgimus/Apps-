"""
Alert service — delivers trading events via Slack webhook and/or email.

All delivery failures are caught and logged as warnings.  If no channels
are configured the service is a no-op.  Callers never need to guard
against exceptions from this module.

Configuration (environment variables)
──────────────────────────────────────
  ALERT_SLACK_WEBHOOK_URL     Full Slack incoming-webhook URL
  ALERT_EMAIL_FROM            Sender address
  ALERT_EMAIL_TO              Recipient(s), comma-separated
  ALERT_SMTP_HOST             SMTP server hostname
  ALERT_SMTP_PORT             SMTP port (default 587)
  ALERT_SMTP_USER             SMTP login username
  ALERT_SMTP_PASSWORD         SMTP login password
  ALERT_SMTP_USE_TLS          "true" (default) for STARTTLS, "false" for SSL
  ALERT_MIN_LEVEL             Minimum level to deliver: "info" (default),
                              "warning", or "critical"
"""

from __future__ import annotations

import asyncio
import json
import logging
import smtplib
import urllib.request
from dataclasses import dataclass
from email.mime.text import MIMEText
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class AlertLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertEvent(str, Enum):
    SESSION_STARTED = "session_started"
    SESSION_STOPPED = "session_stopped"
    ORDER_SUBMITTED = "order_submitted"
    ORDER_FILLED = "order_filled"
    ORDER_PARTIAL = "order_partially_filled"
    ORDER_CANCELLED = "order_cancelled"
    ORDER_REJECTED = "order_rejected"
    ORDER_STALE_CANCELLED = "order_stale_cancelled"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    DAILY_LOSS_THRESHOLD = "daily_loss_threshold"
    KILL_SWITCH = "kill_switch_activated"
    API_ERROR = "api_broker_error"
    EOD_LIQUIDATION = "eod_liquidation"
    SESSION_SUMMARY = "session_summary"


# Default level for each event
_EVENT_LEVELS: Dict[AlertEvent, AlertLevel] = {
    AlertEvent.SESSION_STARTED: AlertLevel.INFO,
    AlertEvent.SESSION_STOPPED: AlertLevel.INFO,
    AlertEvent.ORDER_SUBMITTED: AlertLevel.INFO,
    AlertEvent.ORDER_FILLED: AlertLevel.INFO,
    AlertEvent.ORDER_PARTIAL: AlertLevel.INFO,
    AlertEvent.ORDER_CANCELLED: AlertLevel.WARNING,
    AlertEvent.ORDER_REJECTED: AlertLevel.WARNING,
    AlertEvent.ORDER_STALE_CANCELLED: AlertLevel.WARNING,
    AlertEvent.STOP_LOSS: AlertLevel.WARNING,
    AlertEvent.TAKE_PROFIT: AlertLevel.INFO,
    AlertEvent.DAILY_LOSS_THRESHOLD: AlertLevel.CRITICAL,
    AlertEvent.KILL_SWITCH: AlertLevel.CRITICAL,
    AlertEvent.API_ERROR: AlertLevel.WARNING,
    AlertEvent.EOD_LIQUIDATION: AlertLevel.INFO,
    AlertEvent.SESSION_SUMMARY: AlertLevel.INFO,
}

_LEVEL_ORDER = [AlertLevel.INFO, AlertLevel.WARNING, AlertLevel.CRITICAL]
_SLACK_COLORS = {
    AlertLevel.INFO: "#36a64f",
    AlertLevel.WARNING: "#ffcc00",
    AlertLevel.CRITICAL: "#ff0000",
}
_LEVEL_EMOJI = {
    AlertLevel.INFO: "ℹ️",
    AlertLevel.WARNING: "⚠️",
    AlertLevel.CRITICAL: "🚨",
}


@dataclass
class AlertConfig:
    # Slack
    slack_webhook_url: Optional[str] = None

    # Email
    email_from: Optional[str] = None
    email_to: Optional[str] = None       # comma-separated
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_use_tls: bool = True

    # Filtering
    min_level: AlertLevel = AlertLevel.INFO

    @classmethod
    def from_env(cls) -> "AlertConfig":
        import os
        raw_level = os.getenv("ALERT_MIN_LEVEL", "info").lower()
        try:
            min_level = AlertLevel(raw_level)
        except ValueError:
            min_level = AlertLevel.INFO
        return cls(
            slack_webhook_url=os.getenv("ALERT_SLACK_WEBHOOK_URL") or None,
            email_from=os.getenv("ALERT_EMAIL_FROM") or None,
            email_to=os.getenv("ALERT_EMAIL_TO") or None,
            smtp_host=os.getenv("ALERT_SMTP_HOST") or None,
            smtp_port=int(os.getenv("ALERT_SMTP_PORT", "587")),
            smtp_user=os.getenv("ALERT_SMTP_USER") or None,
            smtp_password=os.getenv("ALERT_SMTP_PASSWORD") or None,
            smtp_use_tls=os.getenv("ALERT_SMTP_USE_TLS", "true").lower() != "false",
            min_level=min_level,
        )

    @property
    def slack_enabled(self) -> bool:
        return bool(self.slack_webhook_url)

    @property
    def email_enabled(self) -> bool:
        return bool(self.email_from and self.email_to and self.smtp_host)

    @property
    def any_channel_enabled(self) -> bool:
        return self.slack_enabled or self.email_enabled


class AlertService:
    """
    Delivers trading event alerts over Slack and/or email.

    Usage::

        cfg = AlertConfig.from_env()
        alerts = AlertService(cfg)
        await alerts.send(AlertEvent.ORDER_FILLED, "SPY call filled @ 3.05", data={...})

    Passing no config (or an unconfigured config) produces a no-op service.
    """

    def __init__(self, config: Optional[AlertConfig] = None):
        self._cfg = config or AlertConfig()
        if self._cfg.slack_enabled:
            logger.info("AlertService: Slack channel enabled")
        if self._cfg.email_enabled:
            logger.info("AlertService: Email channel enabled → %s", self._cfg.email_to)
        if not self._cfg.any_channel_enabled:
            logger.info("AlertService: No channels configured — alerts suppressed")

    @property
    def is_configured(self) -> bool:
        return self._cfg.any_channel_enabled

    # ── Public API ─────────────────────────────────────────────────────────────

    async def send(
        self,
        event: AlertEvent,
        message: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Format and deliver an alert.  Never raises; delivery failures are
        logged as warnings so the trading session is never disrupted.
        """
        if not self._cfg.any_channel_enabled:
            return

        level = _EVENT_LEVELS.get(event, AlertLevel.INFO)
        if _LEVEL_ORDER.index(level) < _LEVEL_ORDER.index(self._cfg.min_level):
            return

        subject, body = self._format(event, level, message, data)

        loop = asyncio.get_event_loop()

        if self._cfg.slack_enabled:
            try:
                await loop.run_in_executor(
                    None, self._send_slack_sync, subject, body, level
                )
                logger.debug("Alert(slack) sent: %s", event.value)
            except Exception as exc:
                logger.warning("Alert(slack) failed [%s]: %s", event.value, exc)

        if self._cfg.email_enabled:
            try:
                await loop.run_in_executor(
                    None, self._send_email_sync, subject, body
                )
                logger.debug("Alert(email) sent: %s", event.value)
            except Exception as exc:
                logger.warning("Alert(email) failed [%s]: %s", event.value, exc)

    # ── Formatting ─────────────────────────────────────────────────────────────

    def _format(
        self,
        event: AlertEvent,
        level: AlertLevel,
        message: str,
        data: Optional[Dict[str, Any]],
    ) -> tuple[str, str]:
        emoji = _LEVEL_EMOJI.get(level, "•")
        subject = f"{emoji} [{level.value.upper()}] {event.value} — {message[:80]}"
        lines = [
            f"Event  : {event.value}",
            f"Level  : {level.value}",
            f"Message: {message}",
        ]
        if data:
            lines.append("")
            lines.append("Details:")
            for k, v in data.items():
                lines.append(f"  {k}: {v}")
        return subject, "\n".join(lines)

    # ── Delivery ───────────────────────────────────────────────────────────────

    def _send_slack_sync(self, subject: str, body: str, level: AlertLevel) -> None:
        payload = json.dumps({
            "attachments": [{
                "color": _SLACK_COLORS[level],
                "title": subject,
                "text": body,
                "footer": "paper-trading · unattended mode",
            }]
        }).encode("utf-8")
        req = urllib.request.Request(
            self._cfg.slack_webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status not in (200, 201):
                raise RuntimeError(f"Slack returned HTTP {resp.status}")

    def _send_email_sync(self, subject: str, body: str) -> None:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = self._cfg.email_from
        msg["To"] = self._cfg.email_to

        recipients = [r.strip() for r in self._cfg.email_to.split(",") if r.strip()]

        if self._cfg.smtp_use_tls:
            smtp = smtplib.SMTP(self._cfg.smtp_host, self._cfg.smtp_port, timeout=15)
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
        else:
            smtp = smtplib.SMTP_SSL(self._cfg.smtp_host, self._cfg.smtp_port, timeout=15)

        try:
            if self._cfg.smtp_user and self._cfg.smtp_password:
                smtp.login(self._cfg.smtp_user, self._cfg.smtp_password)
            smtp.sendmail(self._cfg.email_from, recipients, msg.as_string())
        finally:
            smtp.quit()
