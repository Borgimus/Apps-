"""
Tests for app/utils/alerting.py

Covers:
- AlertConfig.from_env() — all env vars, missing vars, level parsing
- AlertService no-op when unconfigured
- Level filtering (min_level)
- Slack delivery (mocked urllib.request)
- Email delivery (mocked smtplib)
- Alert formatting (subject, body, data section)
- AlertEvent + AlertLevel enums
"""

from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import MagicMock, patch

import pytest

from app.utils.alerting import (
    AlertConfig,
    AlertEvent,
    AlertLevel,
    AlertService,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cfg(
    slack: str | None = None,
    email_from: str | None = None,
    email_to: str | None = None,
    smtp_host: str | None = None,
    min_level: AlertLevel = AlertLevel.INFO,
) -> AlertConfig:
    return AlertConfig(
        slack_webhook_url=slack,
        email_from=email_from,
        email_to=email_to,
        smtp_host=smtp_host,
        smtp_port=587,
        smtp_user=None,
        smtp_password=None,
        smtp_use_tls=True,
        min_level=min_level,
    )


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── AlertConfig ───────────────────────────────────────────────────────────────

class TestAlertConfig:
    def test_no_channels_unconfigured(self):
        cfg = _cfg()
        assert not cfg.slack_enabled
        assert not cfg.email_enabled
        assert not cfg.any_channel_enabled

    def test_slack_enabled_when_url_set(self):
        cfg = _cfg(slack="https://hooks.slack.com/services/TEST")
        assert cfg.slack_enabled
        assert cfg.any_channel_enabled

    def test_email_requires_all_three_fields(self):
        assert not _cfg(email_from="a@b.com").email_enabled
        assert not _cfg(email_to="a@b.com").email_enabled
        assert not _cfg(smtp_host="smtp.example.com").email_enabled
        assert _cfg(
            email_from="from@x.com", email_to="to@x.com", smtp_host="smtp.x.com"
        ).email_enabled

    def test_from_env_reads_slack(self, monkeypatch):
        monkeypatch.setenv("ALERT_SLACK_WEBHOOK_URL", "https://hooks.slack.com/X")
        cfg = AlertConfig.from_env()
        assert cfg.slack_webhook_url == "https://hooks.slack.com/X"

    def test_from_env_reads_email(self, monkeypatch):
        monkeypatch.setenv("ALERT_EMAIL_FROM", "bot@test.com")
        monkeypatch.setenv("ALERT_EMAIL_TO", "me@test.com")
        monkeypatch.setenv("ALERT_SMTP_HOST", "smtp.test.com")
        monkeypatch.setenv("ALERT_SMTP_PORT", "465")
        monkeypatch.setenv("ALERT_SMTP_USE_TLS", "false")
        cfg = AlertConfig.from_env()
        assert cfg.email_from == "bot@test.com"
        assert cfg.email_to == "me@test.com"
        assert cfg.smtp_host == "smtp.test.com"
        assert cfg.smtp_port == 465
        assert cfg.smtp_use_tls is False

    def test_from_env_min_level_warning(self, monkeypatch):
        monkeypatch.setenv("ALERT_MIN_LEVEL", "warning")
        cfg = AlertConfig.from_env()
        assert cfg.min_level == AlertLevel.WARNING

    def test_from_env_min_level_invalid_falls_back_to_info(self, monkeypatch):
        monkeypatch.setenv("ALERT_MIN_LEVEL", "nonsense")
        cfg = AlertConfig.from_env()
        assert cfg.min_level == AlertLevel.INFO

    def test_from_env_blank_url_treated_as_none(self, monkeypatch):
        monkeypatch.setenv("ALERT_SLACK_WEBHOOK_URL", "")
        cfg = AlertConfig.from_env()
        assert cfg.slack_webhook_url is None
        assert not cfg.slack_enabled


# ── AlertService — no-op when unconfigured ────────────────────────────────────

class TestAlertServiceNoop:
    def test_send_does_not_raise_when_unconfigured(self):
        svc = AlertService()
        _run(svc.send(AlertEvent.SESSION_STARTED, "test"))

    def test_is_configured_false_when_no_channels(self):
        svc = AlertService()
        assert not svc.is_configured

    def test_is_configured_true_with_slack(self):
        svc = AlertService(_cfg(slack="https://hooks.slack.com/X"))
        assert svc.is_configured

    def test_send_returns_immediately_without_delivery_when_unconfigured(self):
        svc = AlertService()
        with patch.object(svc, "_send_slack_sync", side_effect=AssertionError("should not call")):
            _run(svc.send(AlertEvent.ORDER_FILLED, "filled"))


# ── Level filtering ───────────────────────────────────────────────────────────

class TestAlertLevelFiltering:
    def _make_slack_svc(self, min_level: AlertLevel) -> tuple[AlertService, list]:
        calls: list = []
        cfg = _cfg(slack="https://hooks.slack.com/X", min_level=min_level)
        svc = AlertService(cfg)

        def fake_slack(subject, body, level):
            calls.append((subject, body, level))

        svc._send_slack_sync = fake_slack
        return svc, calls

    def test_info_event_passes_at_info_min(self):
        svc, calls = self._make_slack_svc(AlertLevel.INFO)
        _run(svc.send(AlertEvent.ORDER_FILLED, "filled"))
        assert len(calls) == 1

    def test_info_event_blocked_at_warning_min(self):
        svc, calls = self._make_slack_svc(AlertLevel.WARNING)
        _run(svc.send(AlertEvent.ORDER_FILLED, "filled"))
        assert len(calls) == 0

    def test_warning_event_passes_at_warning_min(self):
        svc, calls = self._make_slack_svc(AlertLevel.WARNING)
        _run(svc.send(AlertEvent.ORDER_CANCELLED, "cancelled"))
        assert len(calls) == 1

    def test_critical_event_always_passes(self):
        for min_lvl in (AlertLevel.INFO, AlertLevel.WARNING, AlertLevel.CRITICAL):
            svc, calls = self._make_slack_svc(min_lvl)
            _run(svc.send(AlertEvent.KILL_SWITCH, "activated"))
            assert len(calls) == 1, f"Expected delivery at min_level={min_lvl}"

    def test_daily_loss_threshold_is_critical(self):
        svc, calls = self._make_slack_svc(AlertLevel.WARNING)
        _run(svc.send(AlertEvent.DAILY_LOSS_THRESHOLD, "hit"))
        assert len(calls) == 1


# ── Alert formatting ──────────────────────────────────────────────────────────

class TestAlertFormatting:
    def _format(self, event, message, data=None):
        svc = AlertService()
        from app.utils.alerting import _EVENT_LEVELS
        level = _EVENT_LEVELS.get(event, AlertLevel.INFO)
        return svc._format(event, level, message, data)

    def test_subject_contains_event_name(self):
        subject, _ = self._format(AlertEvent.ORDER_FILLED, "SPY filled @ 3.05")
        assert "order_filled" in subject

    def test_subject_contains_level(self):
        subject, _ = self._format(AlertEvent.ORDER_FILLED, "msg")
        assert "INFO" in subject

    def test_subject_contains_message_truncated(self):
        long_msg = "x" * 200
        subject, _ = self._format(AlertEvent.SESSION_STARTED, long_msg)
        assert len(subject) < 300  # truncated

    def test_body_contains_event(self):
        _, body = self._format(AlertEvent.STOP_LOSS, "SL hit")
        assert "stop_loss" in body

    def test_body_contains_message(self):
        _, body = self._format(AlertEvent.ORDER_REJECTED, "rejected — no liquidity")
        assert "rejected — no liquidity" in body

    def test_body_contains_data_fields(self):
        _, body = self._format(
            AlertEvent.ORDER_FILLED,
            "filled",
            data={"fill_price": 3.05, "qty": 1},
        )
        assert "fill_price" in body
        assert "3.05" in body

    def test_body_omits_details_section_when_no_data(self):
        _, body = self._format(AlertEvent.SESSION_STOPPED, "stopped")
        assert "Details:" not in body

    def test_critical_event_uses_red_emoji(self):
        subject, _ = self._format(AlertEvent.KILL_SWITCH, "activated")
        assert "🚨" in subject


# ── Slack delivery ────────────────────────────────────────────────────────────

class TestSlackDelivery:
    def _make_svc(self) -> AlertService:
        return AlertService(_cfg(slack="https://hooks.slack.com/TEST"))

    def test_slack_payload_is_valid_json(self):
        svc = self._make_svc()
        captured: list[bytes] = []

        class FakeResp:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): pass

        with patch("urllib.request.urlopen") as mock_open, \
             patch("urllib.request.Request") as mock_req:
            mock_open.return_value = FakeResp()
            mock_req.side_effect = lambda url, data, headers: (captured.append(data), MagicMock())[1]
            svc._send_slack_sync("subject", "body", AlertLevel.INFO)

        assert captured
        payload = json.loads(captured[0])
        assert "attachments" in payload
        assert payload["attachments"][0]["title"] == "subject"

    def test_slack_error_does_not_propagate(self):
        svc = self._make_svc()

        def boom(*a, **kw):
            raise RuntimeError("network error")

        svc._send_slack_sync = boom
        # send() wraps delivery in try/except — must not raise
        _run(svc.send(AlertEvent.API_ERROR, "broker down"))

    def test_bad_http_status_raises_runtime_error(self):
        svc = self._make_svc()

        class BadResp:
            status = 500
            def __enter__(self): return self
            def __exit__(self, *a): pass

        with patch("urllib.request.urlopen", return_value=BadResp()), \
             patch("urllib.request.Request", return_value=MagicMock()):
            with pytest.raises(RuntimeError):
                svc._send_slack_sync("s", "b", AlertLevel.INFO)

    def test_slack_uses_red_color_for_critical(self):
        from app.utils.alerting import _SLACK_COLORS
        assert _SLACK_COLORS[AlertLevel.CRITICAL] == "#ff0000"

    def test_slack_uses_green_color_for_info(self):
        from app.utils.alerting import _SLACK_COLORS
        assert _SLACK_COLORS[AlertLevel.INFO] == "#36a64f"


# ── Email delivery ────────────────────────────────────────────────────────────

class TestEmailDelivery:
    def _make_svc(self, tls: bool = True) -> AlertService:
        return AlertService(
            _cfg(
                email_from="bot@test.com",
                email_to="me@test.com",
                smtp_host="smtp.test.com",
            )
        )

    def test_email_sent_via_starttls(self):
        svc = self._make_svc()
        mock_smtp = MagicMock()
        mock_smtp_cls = MagicMock(return_value=mock_smtp)

        with patch("smtplib.SMTP", mock_smtp_cls):
            svc._send_email_sync("subject", "body")

        mock_smtp.starttls.assert_called_once()
        mock_smtp.sendmail.assert_called_once()

    def test_email_sent_via_ssl_when_tls_false(self):
        cfg = AlertConfig(
            email_from="bot@test.com",
            email_to="me@test.com",
            smtp_host="smtp.test.com",
            smtp_port=465,
            smtp_use_tls=False,
            min_level=AlertLevel.INFO,
        )
        svc = AlertService(cfg)
        mock_smtp = MagicMock()

        with patch("smtplib.SMTP_SSL", return_value=mock_smtp):
            svc._send_email_sync("subject", "body")

        mock_smtp.sendmail.assert_called_once()

    def test_email_login_called_with_credentials(self):
        cfg = AlertConfig(
            email_from="bot@test.com",
            email_to="me@test.com",
            smtp_host="smtp.test.com",
            smtp_user="user",
            smtp_password="pass",
            smtp_use_tls=True,
            min_level=AlertLevel.INFO,
        )
        svc = AlertService(cfg)
        mock_smtp = MagicMock()

        with patch("smtplib.SMTP", return_value=mock_smtp):
            svc._send_email_sync("subject", "body")

        mock_smtp.login.assert_called_once_with("user", "pass")

    def test_email_no_login_without_credentials(self):
        svc = self._make_svc()
        mock_smtp = MagicMock()

        with patch("smtplib.SMTP", return_value=mock_smtp):
            svc._send_email_sync("subject", "body")

        mock_smtp.login.assert_not_called()

    def test_email_error_does_not_propagate(self):
        svc = self._make_svc()

        def boom(*a, **kw):
            raise ConnectionRefusedError("smtp down")

        svc._send_email_sync = boom
        _run(svc.send(AlertEvent.SESSION_STARTED, "started"))

    def test_email_multiple_recipients(self):
        cfg = AlertConfig(
            email_from="bot@test.com",
            email_to="a@test.com,b@test.com",
            smtp_host="smtp.test.com",
            smtp_use_tls=True,
            min_level=AlertLevel.INFO,
        )
        svc = AlertService(cfg)
        mock_smtp = MagicMock()

        with patch("smtplib.SMTP", return_value=mock_smtp):
            svc._send_email_sync("subject", "body")

        _, recipients, _ = mock_smtp.sendmail.call_args[0]
        assert "a@test.com" in recipients
        assert "b@test.com" in recipients


# ── All 15 AlertEvents have a default level ───────────────────────────────────

class TestAlertEventCoverage:
    def test_all_events_have_default_level(self):
        from app.utils.alerting import _EVENT_LEVELS
        for event in AlertEvent:
            assert event in _EVENT_LEVELS, f"Missing default level for {event}"

    def test_kill_switch_is_critical(self):
        from app.utils.alerting import _EVENT_LEVELS
        assert _EVENT_LEVELS[AlertEvent.KILL_SWITCH] == AlertLevel.CRITICAL

    def test_daily_loss_threshold_is_critical(self):
        from app.utils.alerting import _EVENT_LEVELS
        assert _EVENT_LEVELS[AlertEvent.DAILY_LOSS_THRESHOLD] == AlertLevel.CRITICAL

    def test_order_rejected_is_warning(self):
        from app.utils.alerting import _EVENT_LEVELS
        assert _EVENT_LEVELS[AlertEvent.ORDER_REJECTED] == AlertLevel.WARNING

    def test_order_filled_is_info(self):
        from app.utils.alerting import _EVENT_LEVELS
        assert _EVENT_LEVELS[AlertEvent.ORDER_FILLED] == AlertLevel.INFO
