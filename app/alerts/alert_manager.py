"""
Alert manager.

Dispatches ICT trading alerts to one or more channels:
  • in_app  — stores alert in the SQLite database (always active)
  • email   — SMTP (configurable; disabled by default)
  • discord — webhook URL
  • telegram — bot token + chat_id

Usage
─────
  mgr = AlertManager(settings=alert_settings)
  await mgr.send(AlertMessage(title="Sweep detected", ...))
"""

from __future__ import annotations

import asyncio
import json
import logging
import smtplib
import ssl
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List, Optional

import aiohttp

logger = logging.getLogger(__name__)


# ── Alert message ──────────────────────────────────────────────────────────────

@dataclass
class AlertMessage:
    title: str
    body: str
    symbol: str
    signal_type: str          # e.g. "SWEEP", "FVG_ENTRY", "BACKTEST_COMPLETE"
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "body": self.body,
            "symbol": self.symbol,
            "signal_type": self.signal_type,
            "timestamp": self.timestamp.isoformat(),
            "extra": self.extra,
        }

    def format_text(self) -> str:
        return (
            f"[{self.signal_type}] {self.title}\n"
            f"Symbol: {self.symbol}\n"
            f"Time: {self.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
            f"{self.body}"
        )


# ── Channel config ────────────────────────────────────────────────────────────

@dataclass
class EmailConfig:
    enabled: bool = False
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    username: str = ""
    password: str = ""
    from_addr: str = ""
    to_addrs: List[str] = field(default_factory=list)
    use_tls: bool = True


@dataclass
class DiscordConfig:
    enabled: bool = False
    webhook_url: str = ""


@dataclass
class TelegramConfig:
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""


@dataclass
class AlertSettings:
    in_app: bool = True           # always store in DB
    email: EmailConfig = field(default_factory=EmailConfig)
    discord: DiscordConfig = field(default_factory=DiscordConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)

    @classmethod
    def from_dict(cls, d: dict) -> "AlertSettings":
        settings = cls()
        settings.in_app = d.get("in_app", True)
        if "email" in d:
            settings.email = EmailConfig(**d["email"])
        if "discord" in d:
            settings.discord = DiscordConfig(**d["discord"])
        if "telegram" in d:
            settings.telegram = TelegramConfig(**d["telegram"])
        return settings


# ── In-app store (simple in-memory + optional DB write) ──────────────────────

class InAppStore:
    """Keeps a bounded in-memory list of recent alerts; DB write is optional."""

    def __init__(self, max_alerts: int = 500):
        self._alerts: List[dict] = []
        self._max = max_alerts

    def add(self, msg: AlertMessage) -> None:
        self._alerts.append(msg.to_dict())
        if len(self._alerts) > self._max:
            self._alerts.pop(0)

    def recent(self, n: int = 50) -> List[dict]:
        return list(reversed(self._alerts[-n:]))

    def clear(self) -> None:
        self._alerts.clear()


# ── Alert manager ─────────────────────────────────────────────────────────────

class AlertManager:
    """
    Dispatches AlertMessage objects to all configured channels.

    Parameters
    ----------
    settings : AlertSettings
    db_session_factory : callable | None
        Async callable that returns an AsyncSession (for in-app DB writes).
        If None, alerts are only stored in memory.
    """

    def __init__(
        self,
        settings: AlertSettings | None = None,
        db_session_factory=None,
    ):
        self._cfg = settings or AlertSettings()
        self._db_factory = db_session_factory
        self._store = InAppStore()

    # ── Public API ────────────────────────────────────────────────────────────

    async def send(self, msg: AlertMessage) -> None:
        """Dispatch alert to all enabled channels concurrently."""
        tasks = []

        if self._cfg.in_app:
            tasks.append(self._send_in_app(msg))
        if self._cfg.email.enabled:
            tasks.append(self._send_email(msg))
        if self._cfg.discord.enabled:
            tasks.append(self._send_discord(msg))
        if self._cfg.telegram.enabled:
            tasks.append(self._send_telegram(msg))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, r in enumerate(results):
                if isinstance(r, Exception):
                    logger.error("Alert channel %d failed: %s", i, r)

    def send_sync(self, msg: AlertMessage) -> None:
        """Synchronous convenience wrapper."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self.send(msg))
            else:
                loop.run_until_complete(self.send(msg))
        except RuntimeError:
            asyncio.run(self.send(msg))

    def recent_alerts(self, n: int = 50) -> List[dict]:
        return self._store.recent(n)

    # ── Channel implementations ───────────────────────────────────────────────

    async def _send_in_app(self, msg: AlertMessage) -> None:
        self._store.add(msg)
        logger.info("[ALERT] %s | %s | %s", msg.signal_type, msg.symbol, msg.title)

        if self._db_factory is not None:
            try:
                async with self._db_factory() as session:
                    from ..api.models import DBRiskEvent
                    event = DBRiskEvent(
                        event_type=f"ict_alert_{msg.signal_type.lower()}",
                        symbol=msg.symbol,
                        message=msg.body[:2000],
                        severity="info",
                        timestamp=msg.timestamp,
                    )
                    session.add(event)
                    await session.commit()
            except Exception as exc:
                logger.warning("Failed to persist alert to DB: %s", exc)

    async def _send_email(self, msg: AlertMessage) -> None:
        cfg = self._cfg.email
        if not cfg.username or not cfg.to_addrs:
            logger.warning("Email alert skipped: missing credentials or recipients")
            return

        mime = MIMEMultipart("alternative")
        mime["Subject"] = f"[ICT Alert] {msg.title}"
        mime["From"] = cfg.from_addr or cfg.username
        mime["To"] = ", ".join(cfg.to_addrs)

        text_part = MIMEText(msg.format_text(), "plain")
        html_body = (
            f"<h3>{msg.title}</h3>"
            f"<p><b>Symbol:</b> {msg.symbol}<br>"
            f"<b>Signal:</b> {msg.signal_type}<br>"
            f"<b>Time:</b> {msg.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}</p>"
            f"<pre>{msg.body}</pre>"
        )
        html_part = MIMEText(html_body, "html")
        mime.attach(text_part)
        mime.attach(html_part)

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, self._smtp_send, cfg, mime.as_string(), cfg.to_addrs
        )
        logger.debug("Email alert sent to %s", cfg.to_addrs)

    def _smtp_send(self, cfg: EmailConfig, message: str, recipients: List[str]) -> None:
        context = ssl.create_default_context()
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port) as server:
            if cfg.use_tls:
                server.starttls(context=context)
            server.login(cfg.username, cfg.password)
            server.sendmail(cfg.username, recipients, message)

    async def _send_discord(self, msg: AlertMessage) -> None:
        cfg = self._cfg.discord
        if not cfg.webhook_url:
            logger.warning("Discord alert skipped: no webhook URL")
            return

        payload = {
            "content": None,
            "embeds": [
                {
                    "title": msg.title,
                    "description": msg.body,
                    "color": 0x00B0F0 if "BULLISH" in msg.signal_type.upper() else 0xFF4444,
                    "fields": [
                        {"name": "Symbol", "value": msg.symbol, "inline": True},
                        {"name": "Signal", "value": msg.signal_type, "inline": True},
                        {
                            "name": "Time",
                            "value": msg.timestamp.strftime("%Y-%m-%d %H:%M UTC"),
                            "inline": True,
                        },
                    ],
                    "footer": {"text": "ICT Strategy Alerts"},
                }
            ],
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    cfg.webhook_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status not in (200, 204):
                        text = await resp.text()
                        logger.error("Discord webhook returned %d: %s", resp.status, text)
                    else:
                        logger.debug("Discord alert sent for %s", msg.symbol)
        except Exception as exc:
            logger.error("Discord alert failed: %s", exc)

    async def _send_telegram(self, msg: AlertMessage) -> None:
        cfg = self._cfg.telegram
        if not cfg.bot_token or not cfg.chat_id:
            logger.warning("Telegram alert skipped: missing bot_token or chat_id")
            return

        text = (
            f"*{msg.title}*\n"
            f"Symbol: `{msg.symbol}`\n"
            f"Signal: `{msg.signal_type}`\n"
            f"Time: `{msg.timestamp.strftime('%Y-%m-%d %H:%M UTC')}`\n\n"
            f"{msg.body}"
        )

        url = f"https://api.telegram.org/bot{cfg.bot_token}/sendMessage"
        payload = {
            "chat_id": cfg.chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.error("Telegram returned %d: %s", resp.status, body)
                    else:
                        logger.debug("Telegram alert sent for %s", msg.symbol)
        except Exception as exc:
            logger.error("Telegram alert failed: %s", exc)
