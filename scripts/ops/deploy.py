#!/usr/bin/env python3
"""Guarded VPS rollout. Run from a fetched release before merging that release."""
from __future__ import annotations

import argparse
import fcntl
import getpass
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

BRANCH = "agent/paper-scaled-sizing-250-10"
BEGIN = "# BEGIN PHASE3 SESSION AUTOMATION"
END = "# END PHASE3 SESSION AUTOMATION"
FROZEN = ("config.yaml", "requirements.lock", "ticker_universe.yaml",
          "app/config/settings.py", "evaluation/phase3_tracking.json")
DEPLOYMENT_STATE = {"maintenance_started": False}


def runtime_file(name):
    p = Path(name)
    return (name.startswith("logs/") or name.startswith("evaluation/reports/")
            or name in ("evaluation/shadow_book.jsonl", "evaluation/market_data_comparisons.jsonl")
            or p.parent == Path("evaluation") and p.name.startswith("ledger") and p.suffix == ".json")


def cron_replace(original, replacement):
    lines = original.splitlines()
    if lines.count(BEGIN) != 1 or lines.count(END) != 1:
        raise ValueError("Expected exactly one existing Phase 3 cron block")
    start, stop = lines.index(BEGIN), lines.index(END)
    if start >= stop:
        raise ValueError("Invalid Phase 3 cron block")
    outside = lines[:start] + lines[stop + 1:]
    if any(not line.lstrip().startswith("#") and any(token in line for token in
           ("preflight_session", "start_session", "auto_close_session", "scripts/ops/")) for line in outside):
        raise ValueError("Additional trader cron entries outside the managed block need review")
    return "\n".join(lines[:start] + replacement.splitlines() + lines[stop + 1:]) + "\n"


def backup_tree(source, destination):
    if not source.is_dir():
        return
    shutil.copytree(source, destination, symlinks=True)
    for p in source.rglob("*"):
        if p.is_file() and not p.is_symlink():
            copied = destination / p.relative_to(source)
            with p.open("rb") as a, copied.open("rb") as b:
                if hashlib.file_digest(a, "sha256").digest() != hashlib.file_digest(b, "sha256").digest():
                    raise ValueError("Backup verification failed")


def main():
    DEPLOYMENT_STATE["maintenance_started"] = False
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True, help="Full SHA already fetched from the reviewed release branch")
    args = parser.parse_args()
    if os.geteuid() != 0 or not re.fullmatch(r"[0-9a-f]{40}", args.release):
        raise ValueError("Run as root with an exact 40-character release SHA")
    now = datetime.now(ZoneInfo("America/New_York"))
    if now.weekday() < 5 and 9 <= now.hour < 13:
        raise ValueError("Deploy after 13:00 ET or before 09:00 ET, outside session automation")
    root = Path("/root/trader")
    python = root / ".venv/bin/python"
    os.chdir(root)

    def run(*command, check=True, capture=False, input=None):
        return subprocess.run(command, check=check, text=True, input=input,
                              stdout=subprocess.PIPE if capture else None,
                              stderr=subprocess.PIPE if capture else None)

    def git(*command):
        return run("git", *command, capture=True).stdout.strip()

    def assert_stopped():
        result = run("pgrep", "-af", r"[s]cripts/session_runner[.]py([[:space:]]|$)", check=False, capture=True)
        if result.returncode != 1:
            raise ValueError("Runner active or process check unavailable; deployment stopped")

    if git("branch", "--show-current") != BRANCH:
        raise ValueError("VPS is on an unexpected code branch")
    if git("diff", "--cached", "--name-only"):
        raise ValueError("Staged changes exist; preserve and review them before deployment")
    assert_stopped()
    git("fetch", "origin", f"refs/heads/{BRANCH}:refs/remotes/origin/{BRANCH}")
    if git("rev-parse", "HEAD") != git("rev-parse", f"origin/{BRANCH}"):
        raise ValueError("VPS and code branch differ; synchronize reviewed commits before deployment")
    if run("git", "merge-base", "--is-ancestor", "HEAD", args.release, check=False, capture=True).returncode:
        raise ValueError("Release does not include current deployed code; rebuild release on current branch")
    frozen = [*FROZEN, "app/brokers"]
    if git("diff", "--name-only", "HEAD", args.release, "--", *frozen):
        raise ValueError("Release changes frozen configuration or broker adapters; separate review required")
    dirty = git("diff", "--name-only", "--no-renames").splitlines()
    if any(not runtime_file(p) for p in dirty):
        raise ValueError("Uncommitted source changes exist; deployment stopped")
    cron = run("crontab", "-l", capture=True).stdout
    paused = cron_replace(cron, BEGIN + "\n# Paused for operations deployment\n" + END)
    ready = cron_replace(cron, "\n".join((BEGIN, "CRON_TZ=America/New_York",
        "20 9 * * 1-5 /root/preflight_session.sh", "30 9 * * 1-5 /root/start_session.sh",
        "35 12 * * 1-5 /root/auto_close_session.sh",
        "* * * * * /bin/bash /root/trader/scripts/ops/watchdog.sh",
        "40,50 12 * * 1-5 /bin/bash /root/trader/scripts/ops/publish.sh", END)))

    # Read credentials locally. Never print a topic URL or token.
    from dotenv import dotenv_values
    ops_env = Path("/root/trader-ops.env")
    config = {**dotenv_values(root / ".env"), **(dotenv_values(ops_env) if ops_env.exists() else {})}
    url = config.get("OPS_NTFY_URL") or config.get("NTFY_URL") or ""
    token = config.get("OPS_NTFY_TOKEN") or config.get("NTFY_TOKEN") or ""
    if not url:
        if not sys.stdin.isatty():
            raise ValueError("Set OPS_NTFY_URL in /root/trader-ops.env before noninteractive deployment")
        url = getpass.getpass("Existing ntfy HTTPS topic URL (hidden): ").strip()
        token = getpass.getpass("ntfy access token (hidden; Enter if none): ").strip()
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or "\n" in url + token:
        raise ValueError("Invalid notification configuration")
    evidence = Path(config.get("TRADER_EVIDENCE_DIR") or "/root/trader-evidence")
    checkout = Path(config.get("TRADER_ARTIFACT_CHECKOUT") or "/root/trader-artifacts")
    if evidence != Path("/root/trader-evidence") or checkout != Path("/root/trader-artifacts"):
        raise ValueError("Custom artifact paths require manual rollout review")
    if (evidence / "migration.json").exists():
        raise ValueError("Evidence is already migrated; use a reviewed upgrade instead of repeating initial rollout")

    with ExitStack() as locks:
        for name in (".eod_close.lock", ".session.lock", ".session-preflight.lock"):
            handle = locks.enter_context((Path("/root") / name).open("a"))
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert_stopped()
        # Confirm identity and the full account before editing installed automation.
        code = '''import asyncio, os
from dotenv import load_dotenv
load_dotenv(".env")
os.environ.update(BROKER="alpaca", LIVE_TRADING_ENABLED="false")
from app.config import get_settings
from app.brokers.factory import get_broker
async def check():
    broker=get_broker(get_settings())
    try:
        async with asyncio.timeout(20):
            if not broker.verify_paper_endpoint()[0] or not (await broker.get_account()).is_paper:
                raise RuntimeError("Paper account verification failed")
            for path,params in (("/v2/positions",None),("/v2/orders",{"status":"open","limit":1})):
                r=await broker._client.get(path,params=params); r.raise_for_status()
                if r.json() != []: raise RuntimeError("Account is not flat")
    finally: await broker.close()
asyncio.run(check())
'''
        run(str(python), "-c", code)
        backup = Path("/root/trader-backups") / ("operations-" + now.strftime("%Y%m%dT%H%M%S%z"))
        backup.mkdir(parents=True, mode=0o700)
        backup.chmod(0o700)
        print(f"Backup directory: {backup}", flush=True)
        for name in ("logs", "evaluation"):
            backup_tree(root / name, backup / name)
        for name in ("preflight_session.sh", "start_session.sh", "auto_close_session.sh", "trader-ops.env"):
            source = Path("/root") / name
            if source.exists(): shutil.copy2(source, backup / name)
        (backup / "crontab.txt").write_text(cron)
        (backup / "working-tree.patch").write_text(run("git", "diff", "--binary", capture=True).stdout)
        (backup / "original-head.txt").write_text(git("rev-parse", "HEAD") + "\n")
        run("git", "bundle", "create", str(backup / "repository.bundle"), "--all")
        (root / "KILL_SWITCH").touch()
        (Path("/root") / ".session_armed").unlink(missing_ok=True)
        run("crontab", "-", input=paused)
        DEPLOYMENT_STATE["maintenance_started"] = True
        print("Trading disarmed; session cron paused until validation passes.", flush=True)
        # The backup above was byte-verified. Only known runtime paths are restored.
        if dirty:
            run("git", "restore", "--source=HEAD", "--worktree", "--", *dirty)
        run("git", "merge", "--ff-only", args.release)
        values = {"TRADER_EVIDENCE_DIR": str(evidence), "TRADER_ARTIFACT_CHECKOUT": str(checkout),
                  "OPS_NTFY_URL": url, "OPS_NTFY_TOKEN": token}
        temporary = ops_env.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write("".join(f"{key}={shlex.quote(value)}\n" for key, value in values.items()))
        temporary.replace(ops_env)
        for name, script in (("preflight_session.sh", "preflight"), ("start_session.sh", "start"), ("auto_close_session.sh", "close")):
            destination = Path("/root") / name
            temporary = destination.with_suffix(".tmp")
            temporary.write_text(f'#!/usr/bin/env bash\nexec bash /root/trader/scripts/ops/{script}.sh "$@"\n')
            temporary.chmod(0o700)
            temporary.replace(destination)
        # Keep cron paused on every subsequent failure. Local backups remain available.
    run(str(python), "scripts/session_ops.py", "migrate", "--source-evaluation", str(backup / "evaluation"))
    run(str(python), "scripts/session_ops.py", "check-config")
    run(str(python), "scripts/session_ops.py", "verify-flat")
    run(str(python), "scripts/session_ops.py", "test-alert", "--message", "Trader operations rollout test: deployment verification in progress")
    # Publish a completed historical session before enabling publication cron.
    dates = sorted(p.stem for p in (evidence / "reports").glob("*.json")
                   if re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.stem) and p.stem < str(now.date()))
    if not dates:
        raise ValueError("No completed report found for artifact publication verification")
    run(str(python), "scripts/session_ops.py", "publish", "--date", dates[-1])
    # Promotion is non-forced; a remote race stops here with cron still paused.
    run("git", "push", "origin", f"HEAD:refs/heads/{BRANCH}")
    run("bash", "/root/preflight_session.sh", "--check-only")
    if not (root / "KILL_SWITCH").exists() or Path("/root/.session_armed").exists():
        raise ValueError("Check-only preflight did not leave trading disarmed")
    if git("rev-parse", "HEAD") != git("rev-parse", f"origin/{BRANCH}"):
        raise ValueError("Code branch is not synchronized after preflight")
    run("crontab", "-", input=ready)
    print("DEPLOYED_PREFLIGHT_READY: code synchronized; automation installed; trading remains disarmed.")
    print("Verify the test alert arrived on your device. The next scheduled preflight controls arming.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Avoid displaying notification credentials or credential-bearing Git errors.
        print(f"DEPLOYMENT_STOPPED: {type(exc).__name__}", file=sys.stderr)
        if isinstance(exc, ValueError): print(str(exc), file=sys.stderr)
        if DEPLOYMENT_STATE["maintenance_started"]:
            print("Do not start a session. Preserve the backup and inspect deployment output; paused cron is not automatically restored.", file=sys.stderr)
        else:
            print("Stopped before cron maintenance or code installation. The existing schedule was not paused by this attempt.", file=sys.stderr)
        raise SystemExit(1)
