"""Notifications: full event catalog, redaction, in-memory + dispatcher fan-out."""
from src.notifications.interface import (
    Event,
    Notification,
    Severity,
    redact,
)
from src.notifications.providers import (
    InMemoryNotifier,
    NotificationDispatcher,
    WebhookNotifier,
)

REQUIRED_EVENTS = {
    "IMPORT_ACCEPTED", "IMPORT_REJECTED", "SETUP_QUALIFIED", "ENTRY_SUBMITTED", "ENTRY_FILLED",
    "ENTRY_CANCELED", "ENTRY_REJECTED", "STOP_MISSING", "STOP_RECOVERED", "PARTIAL_5R_SUBMITTED",
    "PARTIAL_5R_FILLED", "STOP_MOVED_BREAKEVEN", "DAILY_CLOSE_EXIT_TRIGGERED",
    "DAILY_CLOSE_EXIT_FILLED", "BROKER_DISCONNECT", "DATA_DISCONNECT", "RECON_MISMATCH",
    "PROCESS_START", "PROCESS_RESTART", "PROCESS_SHUTDOWN",
}


def test_event_catalog_covers_all_required_events():
    assert REQUIRED_EVENTS <= {e.value for e in Event}


def test_redaction_masks_secret_keys_and_values():
    data = {"api_secret": "supersecret", "note": "Bearer abcdef0123456789",
            "nested": {"password": "p"}, "qty": 100}
    out = redact(data)
    assert out["api_secret"] == "***REDACTED***"
    assert "abcdef0123456789" not in out["note"]
    assert out["nested"]["password"] == "***REDACTED***"
    assert out["qty"] == 100


def test_safe_payload_is_redacted():
    n = Notification(Event.ENTRY_SUBMITTED, Severity.INFO, "entry", "submitted",
                     data={"authorization": "Bearer zzzzzzzzzzzzzzzz", "symbol": "AAA"})
    payload = n.safe_payload()
    assert payload["data"]["authorization"] == "***REDACTED***"
    assert payload["data"]["symbol"] == "AAA"
    assert payload["event"] == "ENTRY_SUBMITTED"


def test_in_memory_notifier_records():
    nf = InMemoryNotifier()
    nf.send(Notification(Event.STOP_MISSING, Severity.CRITICAL, "stop", "missing"))
    assert nf.events() == ["STOP_MISSING"]


def test_webhook_uses_injected_sender_with_redacted_payload():
    sent = {}

    def sender(url, payload):
        sent["url"] = url
        sent["payload"] = payload

    wh = WebhookNotifier("https://hook.example/x", sender)
    wh.send(Notification(Event.RECON_MISMATCH, Severity.CRITICAL, "recon", "mismatch",
                         data={"api_key": "AKIA1234567890ABCDEF"}))
    assert sent["url"] == "https://hook.example/x"
    assert sent["payload"]["data"]["api_key"] == "***REDACTED***"


def test_dispatcher_fans_out_and_isolates_failures():
    good = InMemoryNotifier()
    errors = []

    class Bad(InMemoryNotifier):
        def send(self, notification):
            raise RuntimeError("provider down")

    disp = NotificationDispatcher([Bad(), good], on_error=lambda n, e: errors.append(str(e)))
    disp.send(Notification(Event.PROCESS_START, Severity.INFO, "start", "up"))
    assert good.events() == ["PROCESS_START"]   # good provider still delivered
    assert len(errors) == 1                      # bad provider failure captured, not raised
