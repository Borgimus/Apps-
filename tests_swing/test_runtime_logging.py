"""JSON logging: structured output, extras, and secret redaction."""
import json
import logging

from src.runtime.logging_setup import JsonFormatter, RedactionFilter, configure_logging, scrub


def test_scrub_masks_secrets():
    assert "***REDACTED***" in scrub("api_key=AKIA1234567890ABCDEF")
    assert "***REDACTED***" in scrub("Authorization: Bearer abcdef0123456789")
    assert "***REDACTED***" in scrub("url https://x/y?signature=deadbeefcafebabe0000")
    assert scrub("just a normal message qty=100") == "just a normal message qty=100"


def test_json_formatter_emits_valid_json_with_extras():
    rec = logging.makeLogRecord({"name": "swing", "levelname": "INFO",
                                 "msg": "entry submitted", "symbol": "AAA"})
    out = JsonFormatter().format(rec)
    obj = json.loads(out)
    assert obj["message"] == "entry submitted"
    assert obj["logger"] == "swing"
    assert obj["symbol"] == "AAA"       # structured extra carried through


def test_redaction_filter_scrubs_message_and_args():
    rec = logging.makeLogRecord({"msg": "token=%s", "args": ("sk-abcdefghijklmnopqrstuvwxyz",)})
    RedactionFilter().filter(rec)
    assert "***REDACTED***" in rec.getMessage()


def test_configure_logging_writes_json_lines(tmp_path):
    log_file = tmp_path / "swing.log"
    root = configure_logging(level="INFO", log_file=str(log_file))
    logging.getLogger("swing.test").info("hello secret=AKIA1234567890ABCDEF")
    for h in root.handlers:
        h.flush()
    lines = log_file.read_text().strip().splitlines()
    assert lines, "expected at least one log line"
    obj = json.loads(lines[-1])
    assert "***REDACTED***" in obj["message"] and "AKIA" not in obj["message"]
