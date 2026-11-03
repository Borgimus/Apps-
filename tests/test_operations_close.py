"""Exercise actual close shell logic with fake processes, broker and transport."""
import fcntl
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.operations.monitor import verify_flat


@pytest.mark.asyncio
@pytest.mark.parametrize("positions,orders,expected", [([], [], True),
    ([{"symbol": "SPY", "asset_class": "us_equity"}], [], False),
    ([{"symbol": "unparseable"}], [], False), ([], [{"unexpected": "working order"}], False),
    (None, [], False), ({}, [], False), ([], {}, False)])
async def test_full_account_flat_check_never_discards_exposure(monkeypatch, positions, orders, expected):
    import app.config
    import app.brokers.factory
    monkeypatch.setattr(app.config, "get_settings", lambda: SimpleNamespace(broker="alpaca", live_trading_enabled=False))
    responses = [httpx.Response(200, json=value, request=httpx.Request("GET", "https://paper-api.alpaca.markets"))
                 for value in (positions, orders)]
    broker = SimpleNamespace(verify_paper_endpoint=lambda: (True, "paper"),
                             get_account=AsyncMock(return_value=SimpleNamespace(is_paper=True)),
                             _client=SimpleNamespace(get=AsyncMock(side_effect=responses)), close=AsyncMock())
    monkeypatch.setattr(app.brokers.factory, "get_broker", lambda settings: broker)
    if expected:
        await verify_flat()
        assert broker._client.get.call_args_list[-1].kwargs["params"] == {"status": "open", "limit": 1}
    else:
        with pytest.raises(ValueError):
            await verify_flat()
    broker.close.assert_awaited_once()


@pytest.fixture
def close_host(tmp_path):
    root = tmp_path / "trader"
    scripts = Path(__file__).resolve().parents[1] / "scripts/ops"
    shutil.copytree(scripts, root / "scripts/ops")
    (root / ".env").write_text("")
    locks, fakebin = tmp_path / "locks", tmp_path / "bin"
    locks.mkdir()
    fakebin.mkdir()
    (locks / ".session_armed").write_text("old marker")
    (tmp_path / "active").write_text("0")
    (tmp_path / "checks").write_text("0")
    commands = {
        "pgrep": '#!/bin/bash\nprintf "%s\\n" "$2" >> "$TEST_DIR/patterns"\nif test "$(cat "$TEST_DIR/active")" = 1; then echo 99999999; else exit 1; fi\n',
        "sleep": '#!/bin/bash\nif test "$MODE" = natural; then echo 0 > "$TEST_DIR/active"; fi\n',
        "git": '#!/bin/bash\necho GIT_WAS_CALLED >> "$TEST_DIR/calls"\nexit 99\n',
    }
    for name, script in commands.items():
        path = fakebin / name
        path.write_text(script)
        path.chmod(0o755)
    bashenv = tmp_path / "bashenv"
    bashenv.write_text('kill() { echo "kill $*" >> "$TEST_DIR/calls"; if test "$MODE" = graceful; then echo 0 > "$TEST_DIR/active"; fi; }\n')
    python = root / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text('''#!/bin/bash
echo "$*" >> "$TEST_DIR/calls"
case "$*" in
  *"session_ops.py verify-flat"*)
    n=$(cat "$TEST_DIR/checks"); n=$((n+1)); echo "$n" > "$TEST_DIR/checks"
    if test "$BROKER_FAIL" = all || { test "$BROKER_FAIL" = final && test "$n" -ge 2; }; then exit 1; fi;;
  *"shadow_report.py"*) test "$REPORT_FAIL" = 0 || exit 1;;
  *"session_ops.py publish"*)
    exec 6>"$TRADER_LOCK_DIR/.session.lock"; flock -n 6 || exit 98
    exec 5>"$TRADER_LOCK_DIR/.eod_close.lock"; if flock -n 5; then exit 97; fi
    echo PUBLISH_SESSION_LOCK_FREE_EOD_HELD >> "$TEST_DIR/calls"
    test "$PUBLISH_FAIL" = 0 || exit 1;;
esac
exit 0
''')
    python.chmod(0o755)
    env = {**os.environ, "TRADER_ROOT": str(root), "TRADER_LOCK_DIR": str(locks),
           "TRADER_OPS_ENV": str(tmp_path / "absent"), "TEST_DIR": str(tmp_path),
           "PATH": str(fakebin) + os.pathsep + os.environ["PATH"], "BASH_ENV": str(bashenv),
           "MODE": "stopped", "BROKER_FAIL": "none", "REPORT_FAIL": "0", "PUBLISH_FAIL": "0"}

    def run(**changes):
        env.update(changes)
        if env["MODE"] != "stopped":
            (tmp_path / "active").write_text("1")
        result = subprocess.run(["bash", str(root / "scripts/ops/close.sh")], env=env, timeout=20)
        calls = (tmp_path / "calls").read_text()
        assert "GIT_WAS_CALLED" not in calls
        assert "kill -KILL" not in calls and "kill -9" not in calls
        assert (root / "KILL_SWITCH").exists()
        assert not (locks / ".session_armed").exists()
        return result.returncode, calls, list((root / "logs").glob("AUTOMATION_FAILURE_*.txt"))
    return run, tmp_path


@pytest.mark.parametrize("mode", ["stopped", "natural", "graceful"])
def test_successful_close_preserves_grace_and_publishes_after_releasing_locks(close_host, mode):
    run, directory = close_host
    rc, calls, failures = run(MODE=mode)
    assert rc == 0 and not failures
    assert calls.count("session_ops.py verify-flat") == 2
    assert "PUBLISH_SESSION_LOCK_FREE_EOD_HELD" in calls
    assert ("kill -TERM" in calls) == (mode == "graceful")
    pattern = (directory / "patterns").read_text().splitlines()[0]
    for command in ("python scripts/session_runner.py --eval", "python /root/trader/scripts/session_runner.py --eval"):
        assert subprocess.run(["grep", "-Eq", pattern], input=command, text=True).returncode == 0


@pytest.mark.parametrize("broker", ["none", "all"])
def test_wedged_runner_remains_alive_even_with_clean_snapshot(close_host, broker):
    run, directory = close_host
    rc, calls, failures = run(MODE="wedged", BROKER_FAIL=broker)
    assert rc == 1 and failures
    assert "kill -TERM" in calls
    assert "session_ops.py publish" not in calls
    assert "runner_shutdown_failed" in calls
    assert (directory / "active").read_text().strip() == "1"


@pytest.mark.parametrize("changes", [{"BROKER_FAIL": "all"}, {"BROKER_FAIL": "final"},
                                    {"REPORT_FAIL": "1"}, {"PUBLISH_FAIL": "1"}])
def test_failure_keeps_marker_and_preserves_raw_evidence_attempt(close_host, changes):
    run, _ = close_host
    rc, calls, failures = run(**changes)
    assert rc == 1 and failures
    assert "session_ops.py publish" in calls
    assert "eod_failed" in calls


@pytest.mark.parametrize("script", ["preflight.sh", "start.sh"])
def test_close_lock_blocks_new_start_and_preflight_without_changing_state(close_host, script):
    _, directory = close_host
    root, locks = directory / "trader", directory / "locks"
    env = {**os.environ, "TRADER_ROOT": str(root), "TRADER_LOCK_DIR": str(locks),
           "TRADER_OPS_ENV": str(directory / "absent"), "TEST_DIR": str(directory)}
    marker = locks / ".session_armed"
    with (locks / ".eod_close.lock").open("a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        result = subprocess.run(["bash", str(root / "scripts/ops" / script)], env=env, timeout=10)
    assert result.returncode == 75
    assert marker.read_text() == "old marker"
    assert not (root / "KILL_SWITCH").exists()
