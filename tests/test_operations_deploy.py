import importlib.util
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location("operations_deploy", Path(__file__).resolve().parents[1] / "scripts/ops/deploy.py")
d = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(d)


@pytest.mark.parametrize("path,expected", [("logs/trading.jsonl.1", True),
    ("evaluation/shadow_book.jsonl", True), ("evaluation/reports/2026-09-18.md", True),
    ("evaluation/ledger.paper_scaled.json", True), ("evaluation/phase3_tracking.json", False),
    ("config.yaml", False), ("app/trading/order_manager.py", False)])
def test_only_backed_up_runtime_files_can_be_restored(path, expected):
    assert d.runtime_file(path) is expected


def test_cron_replacement_preserves_other_jobs_and_rejects_ambiguity():
    original = "MAILTO=root\n" + d.BEGIN + "\nold job\n" + d.END + "\n0 1 * * * /root/backup.sh\n"
    changed = d.cron_replace(original, d.BEGIN + "\nnew job\n" + d.END)
    assert changed.startswith("MAILTO=root\n")
    assert changed.endswith("0 1 * * * /root/backup.sh\n")
    assert "old job" not in changed
    with pytest.raises(ValueError): d.cron_replace(original + original, "")
    with pytest.raises(ValueError): d.cron_replace(original + "* * * * * /root/start_session.sh\n", "")


@pytest.mark.parametrize("fail_at", [None, "migrate", "publish", "push", "preflight"])
def test_deploy_enables_cron_only_after_all_gates_and_preserves_live_evidence(tmp_path, monkeypatch, fail_at):
    root = tmp_path / "root"
    repo = root / "trader"
    (repo / "logs").mkdir(parents=True)
    (repo / "evaluation/reports").mkdir(parents=True)
    (repo / "logs/trading.jsonl").write_text("latest rotated evidence")
    (repo / "evaluation/shadow_book.jsonl").write_text("latest uncommitted shadow evidence")
    (repo / "evaluation/reports/2026-09-18.json").write_text("{}")
    (repo / ".env").write_text("OPS_NTFY_URL=https://example.test/topic\n")
    for name in ("preflight_session.sh", "start_session.sh", "auto_close_session.sh"):
        (root / name).write_text("old script")
    original = d.BEGIN + "\nCRON_TZ=America/New_York\nold cron\n" + d.END + "\n"
    state = dict(cron=original, local="a"*40, remote="a"*40)
    calls = []

    def mapped(value):
        value = str(value)
        if value == "/root" or value.startswith("/root/"):
            return root / value.removeprefix("/root").lstrip("/")
        return Path(value)
    monkeypatch.setattr(d, "Path", mapped)
    monkeypatch.setattr(d.os, "geteuid", lambda: 0)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(d, "datetime", SimpleNamespace(now=lambda tz: datetime(2026, 9, 21, 14, 0, tzinfo=tz)))
    monkeypatch.setattr(sys, "argv", ["deploy.py", "--release", "b"*40])

    def command(argv, **kwargs):
        calls.append(tuple(argv))
        rc, output = 0, ""
        if argv[0] == "pgrep": rc = 1
        elif argv[0] == "crontab":
            if argv[1] == "-l": output = state["cron"]
            else: state["cron"] = kwargs["input"]
        elif argv[0] == "git":
            if argv[1] == "branch": output = d.BRANCH
            elif argv[1:3] == ("rev-parse", "HEAD"): output = state["local"]
            elif argv[1] == "rev-parse": output = state["remote"]
            elif argv[1:3] == ("diff", "--name-only") and "--no-renames" in argv:
                output = "logs/trading.jsonl\nevaluation/shadow_book.jsonl\n"
            elif argv[1] == "merge": state["local"] = "b"*40
            elif argv[1] == "push":
                if fail_at == "push": rc = 1
                else: state["remote"] = state["local"]
        elif "scripts/session_ops.py" in argv:
            operation = argv[2]
            if fail_at == operation: rc = 1
            elif operation == "migrate":
                source = Path(argv[-1])
                assert source.name == "evaluation" and "trader-backups" in str(source)
                shutil.copytree(source, root / "trader-evidence")
        elif argv[0] == "bash" and fail_at == "preflight": rc = 1
        result = subprocess.CompletedProcess(argv, rc, stdout=output, stderr="")
        if rc and kwargs.get("check", True): raise subprocess.CalledProcessError(rc, argv)
        return result
    monkeypatch.setattr(d.subprocess, "run", command)
    if fail_at:
        with pytest.raises(subprocess.CalledProcessError): d.main()
        assert "Paused for operations deployment" in state["cron"]
        assert "watchdog.sh" not in state["cron"]
    else:
        d.main()
        assert "watchdog.sh" in state["cron"] and "publish.sh" in state["cron"]
        assert state["local"] == state["remote"] == "b"*40
    backup = next((root / "trader-backups").iterdir())
    assert (backup / "logs/trading.jsonl").read_text() == "latest rotated evidence"
    assert (backup / "evaluation/shadow_book.jsonl").read_text() == "latest uncommitted shadow evidence"
    assert (backup / "auto_close_session.sh").read_text() == "old script"
    assert (repo / "KILL_SWITCH").exists()
    assert not (root / ".session_armed").exists()
    assert 'scripts/ops/close.sh "$@"' in (root / "auto_close_session.sh").read_text()
