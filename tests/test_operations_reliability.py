"""Read-only monitoring and actual local-Git artifact isolation regressions."""
import fcntl
import json
import os
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.operations import artifacts as a
from app.operations import monitor as m

NOW = datetime(2026, 9, 18, 9, 35, tzinfo=m.ET)


def heartbeat(now=NOW):
    return {"session_date": str(now.date()), "ts": now.isoformat(), "cycle": 2}


@pytest.mark.parametrize("snapshot", [{}, {"session_active": False},
    heartbeat(NOW-timedelta(days=1)), dict(heartbeat(), ts="2026-09-18T09:35:00"),
    dict(heartbeat(), ts=(NOW-timedelta(seconds=181)).isoformat()),
    dict(heartbeat(), ts=(NOW+timedelta(seconds=1)).isoformat()), dict(heartbeat(), cycle=0)])
def test_invalid_heartbeat_is_not_a_running_session(snapshot):
    assert m.session_problem(snapshot, NOW)


def test_market_closed_and_healthy_session_do_not_alert(tmp_path):
    sent = []
    assert m.watchdog(tmp_path, now=NOW, query=lambda: False, sender=sent.append)
    assert sent == []
    m.calendar(tmp_path, now=NOW, refresh=True, query=lambda: True)
    m.write_json(tmp_path / "logs/live_status.json", heartbeat())
    assert m.watchdog(tmp_path, now=NOW, sender=sent.append)
    assert sent == []


def test_missing_session_alerts_without_a_runner_and_deduplicates(tmp_path):
    sent = []
    for _ in range(2):
        assert not m.watchdog(tmp_path, now=NOW, query=lambda: True, sender=sent.append)
    assert len(sent) == 1
    assert "No active session heartbeat" in sent[0]


def test_delivery_failure_retries_and_never_persists_secret_error(tmp_path):
    def failure(message):
        raise RuntimeError("SECRET_URL_AND_TOKEN")
    assert not m.notify(tmp_path, "preflight_failed", "Preflight failed", now=NOW, sender=failure)
    record = tmp_path / "logs/operations/2026-09-18-preflight_failed.json"
    assert "SECRET_URL_AND_TOKEN" not in record.read_text()
    sent = []
    assert m.watchdog(tmp_path, now=NOW.replace(hour=14), sender=sent.append)
    assert sent == ["Preflight failed"]
    assert json.loads(record.read_text())["attempts"] == 2


def test_calendar_error_is_not_misclassified_as_holiday(tmp_path):
    def failed():
        raise TimeoutError()
    sent = []
    assert not m.watchdog(tmp_path, now=NOW, query=failed, sender=sent.append)
    assert len(sent) == 1 and "calendar" in sent[0]
    assert not list((tmp_path / "logs/operations").glob("calendar-*.json"))


@pytest.fixture
def repos(tmp_path):
    remote, code, evidence, checkout = [tmp_path / name for name in ("remote.git", "code", "evidence", "artifacts")]
    remote.mkdir()
    a.git(remote, "init", "--bare")
    code.mkdir()
    a.git(code, "init", "--initial-branch=agent/paper-scaled-sizing-250-10")
    a.git(code, "config", "user.name", "Test")
    a.git(code, "config", "user.email", "test@localhost")
    a.git(code, "remote", "add", "origin", str(remote))
    (code / "app.txt").write_text("deployed code\n")
    a.git(code, "add", "app.txt")
    a.git(code, "commit", "-m", "Initial code")
    a.git(code, "push", "origin", "HEAD")
    (code / "logs").mkdir()
    (code / "logs/trading.jsonl.1").write_text("ROTATED_LOG_MUST_NOT_PUBLISH")
    (code / ".env").write_text("SECRET_TOKEN")
    (code / "logs/session_2026-09-18.log").write_text("Session completed\n")
    (evidence / "reports").mkdir(parents=True)
    (evidence / "reports/2026-09-18.md").write_text("Session report\n")
    (evidence / "shadow_book.jsonl").write_text('\n'.join(json.dumps(row) for row in [
        {"ts": "2026-09-18T14:00:00+00:00", "event": "selected"},
        {"ts": "2026-09-18T01:00:00+00:00", "event": "previous_ET_day"}]) + '\n')
    return remote, code, evidence, checkout


def test_publish_uses_separate_branch_and_leaves_code_head_index_and_remote_unchanged(repos):
    remote, code, evidence, checkout = repos
    head = a.git(code, "rev-parse", "HEAD").stdout
    index = (code / ".git/index").read_bytes()
    snapshot = a.publish(code, evidence, checkout, "2026-09-18")
    first = a.git(checkout, "rev-parse", "HEAD").stdout
    assert a.publish(code, evidence, checkout, "2026-09-18") == snapshot
    assert a.git(checkout, "rev-parse", "HEAD").stdout == first
    assert a.git(code, "rev-parse", "HEAD").stdout == head
    assert (code / ".git/index").read_bytes() == index
    assert a.git(remote, "rev-parse", "refs/heads/agent/paper-scaled-sizing-250-10").stdout == head
    assert a.git(remote, "rev-parse", "refs/heads/session-artifacts").stdout == first
    manifest = json.loads((snapshot / "manifest.json").read_text())
    assert "logs/trading.jsonl.1" not in manifest["files_sha256"]
    assert ".env" not in manifest["files_sha256"]
    assert "previous_ET_day" not in (snapshot / "shadow_book.jsonl").read_text()
    assert "selected" in (snapshot / "shadow_book.jsonl").read_text()
    # A fresh publisher can bootstrap from the unrelated artifact history.
    a.publish(code, evidence, checkout.parent / "second-publisher", "2026-09-18")


def test_remote_rejection_preserves_archive_and_retry_pushes_same_commit(repos):
    remote, code, evidence, checkout = repos
    hook = remote / "hooks/pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    with pytest.raises(subprocess.CalledProcessError):
        a.publish(code, evidence, checkout, "2026-09-18")
    saved = list((evidence / "archives/2026-09-18").iterdir())
    assert len(saved) == 1
    a.verify_archive(saved[0])
    commit = a.git(checkout, "rev-parse", "HEAD").stdout
    hook.unlink()
    a.publish(code, evidence, checkout, "2026-09-18")
    assert a.git(checkout, "rev-parse", "HEAD").stdout == commit


def test_archive_revisions_are_immutable_and_corruption_fails(repos):
    _, code, evidence, _ = repos
    old = a.archive(code, evidence, "2026-09-18")
    (evidence / "reports/2026-09-18.md").write_text("Revised report\n")
    new = a.archive(code, evidence, "2026-09-18")
    assert old != new
    assert (old / "reports/2026-09-18.md").read_text() == "Session report\n"
    (new / "reports/2026-09-18.md").write_text("Corrupt")
    with pytest.raises(ValueError, match="checksum"):
        a.archive(code, evidence, "2026-09-18")


def test_seed_migration_never_overwrites_newer_evidence(repos):
    _, code, evidence, _ = repos
    (code / "evaluation").mkdir()
    (code / "evaluation/shadow_book.jsonl").write_text("different evidence")
    before = (evidence / "shadow_book.jsonl").read_bytes()
    with pytest.raises(ValueError, match="differs"):
        a.seed_evidence(code, evidence)
    assert (evidence / "shadow_book.jsonl").read_bytes() == before
    assert not (evidence / "migration.json").exists()


def test_remote_code_advance_cannot_deadlock_artifact_publication(repos):
    remote, code, evidence, checkout = repos
    deployed = a.git(code, "rev-parse", "HEAD").stdout
    upstream = code.parent / "upstream"
    a.git(code.parent, "clone", "--branch", "agent/paper-scaled-sizing-250-10", str(remote), str(upstream))
    a.git(upstream, "config", "user.name", "Test")
    a.git(upstream, "config", "user.email", "test@localhost")
    (upstream / "app.txt").write_text("Reviewed future deployment")
    a.git(upstream, "commit", "-am", "Next code release")
    a.git(upstream, "push", "origin", "HEAD")
    new_code = a.git(upstream, "rev-parse", "HEAD").stdout
    a.publish(code, evidence, checkout, "2026-09-18")
    assert a.git(code, "rev-parse", "HEAD").stdout == deployed
    assert a.git(remote, "rev-parse", "refs/heads/agent/paper-scaled-sizing-250-10").stdout == new_code


def test_seed_migration_preserves_history_and_is_idempotent(repos):
    _, code, _, _ = repos
    (code / "evaluation/reports").mkdir(parents=True)
    for name, content in {"shadow_book.jsonl": '{"event":"history"}\n',
                          "ledger.paper_scaled.json": '{"sessions":[1]}',
                          "reports/2026-09-18.md": "Historical report"}.items():
        (code / "evaluation" / name).write_text(content)
    target = code.parent / "new-evidence"
    a.seed_evidence(code, target)
    a.seed_evidence(code, target)
    assert (target / "shadow_book.jsonl").read_bytes() == (code / "evaluation/shadow_book.jsonl").read_bytes()
    assert (target / "ledger.paper_scaled.json").read_text() == '{"sessions":[1]}'
    assert (target / "migration.json").is_file()


def test_migration_can_use_backup_after_git_restores_tracked_runtime_files(repos):
    _, code, _, _ = repos
    (code / "evaluation").mkdir()
    (code / "evaluation/shadow_book.jsonl").write_text("older committed events")
    backup = code.parent / "backup/evaluation"
    backup.mkdir(parents=True)
    (backup / "shadow_book.jsonl").write_text("latest backed-up events")
    target = code.parent / "migrated-from-backup"
    a.seed_evidence(code, target, source_evaluation=backup)
    assert (target / "shadow_book.jsonl").read_text() == "latest backed-up events"


def test_preflight_with_active_session_lock_does_not_disarm(tmp_path):
    source = Path(__file__).resolve().parents[1]
    root, locks = tmp_path / "code", tmp_path / "locks"
    shutil.copytree(source / "scripts/ops", root / "scripts/ops")
    locks.mkdir()
    (root / ".env").write_text("")
    python = root / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\nexit 0\n")
    python.chmod(0o755)
    marker = locks / ".session_armed"
    marker.write_text("existing marker")
    with (locks / ".session.lock").open("a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        result = subprocess.run(["bash", str(root / "scripts/ops/preflight.sh")],
                                env={**os.environ, "TRADER_ROOT": str(root), "TRADER_LOCK_DIR": str(locks),
                                     "TRADER_OPS_ENV": str(tmp_path / "absent.env")}, timeout=10)
    assert result.returncode == 75
    assert marker.read_text() == "existing marker"
    assert not (root / "KILL_SWITCH").exists()
