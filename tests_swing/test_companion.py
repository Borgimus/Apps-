"""Windows companion: complete-batch detection, payload, injected sender, and NO order authority."""
from pathlib import Path

import pytest

from companion.tc2000_watcher import (
    build_upload,
    find_complete_batches,
    post_batch,
)

ROOT = Path(__file__).resolve().parents[1]


def test_complete_batch_detected():
    files = [
        "strength_1m_2026-07-31.txt",
        "strength_3m_2026-07-31.txt",
        "strength_6m_2026-07-31.txt",
        "notes.txt",
    ]
    batches = find_complete_batches(files)
    assert len(batches) == 1
    assert batches[0].market_date == "2026-07-31"
    assert len(batches[0].filenames) == 3


def test_incomplete_batch_ignored():
    files = ["strength_1m_2026-07-31.txt", "strength_3m_2026-07-31.txt"]  # missing 6m
    assert find_complete_batches(files) == []


def test_build_upload_includes_hashes():
    payload = build_upload({"strength_1m_2026-07-31.txt": "AAA\nBBB\n"})
    assert "files" in payload and "hashes" in payload
    assert len(payload["hashes"]["strength_1m_2026-07-31.txt"]) == 64


def test_post_batch_uses_injected_sender_and_bearer():
    captured = {}

    def sender(url, headers, payload):
        captured.update(url=url, headers=headers, payload=payload)
        return "ok"

    out = post_batch("https://svc/ingest", "companion-key", {"files": {}}, sender=sender)
    assert out == "ok"
    assert captured["headers"]["Authorization"] == "Bearer companion-key"


def test_post_batch_requires_url_and_key():
    with pytest.raises(ValueError):
        post_batch("", "k", {}, sender=lambda *a: None)


def test_companion_has_no_broker_or_order_authority():
    # Static guard: the companion must not import broker/execution modules or define order funcs.
    src = (ROOT / "companion" / "tc2000_watcher.py").read_text()
    assert "src.broker" not in src and "src.execution" not in src
    for forbidden in ("submit_order", "place_order", "cancel_order", "replace_order"):
        assert forbidden not in src
