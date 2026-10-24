"""Immutable evidence snapshots published through an isolated artifact repository.

Never stages, commits, merges, rebases or pushes the deployed code checkout.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import date
from pathlib import Path

from app.operations.monitor import write_json

ARTIFACT_BRANCH = "session-artifacts"


def git(repo: Path, *args, check=True):
    return subprocess.run(["git", "-C", str(repo), *args], check=check,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=90)


def outside_code(root: Path, path: Path):
    if path.resolve() == root.resolve() or root.resolve() in path.resolve().parents:
        raise ValueError("Evidence and artifact checkout must be outside the code repository")


def seed_evidence(root: Path, evidence: Path, *, source_evaluation: Path | None = None):
    """Copy original evidence once, preserving historical progress and ledger state.

    Existing unequal files stop migration instead of replacing newer runtime data.
    """
    outside_code(root, evidence)
    source = source_evaluation or root / "evaluation"
    sources = [source / "shadow_book.jsonl", source / "market_data_comparisons.jsonl"]
    sources += sorted(source.glob("ledger*.json"))
    sources += sorted((source / "reports").glob("*"))
    pairs = [(p, evidence / p.relative_to(source)) for p in sources if p.is_file()]
    for src, dst in pairs:
        if dst.exists() and src.read_bytes() != dst.read_bytes():
            raise ValueError(f"Existing evidence differs: {dst.name}; review migration manually")
    for src, dst in pairs:
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    write_json(evidence / "migration.json", {"code_commit": git(root, "rev-parse", "HEAD").stdout.strip(),
                                            "files_copied_or_verified": len(pairs)})


def archive(root: Path, evidence: Path, day: str) -> Path:
    if date.fromisoformat(day).isoformat() != day:
        raise ValueError("Date must be YYYY-MM-DD")
    outside_code(root, evidence)
    archives = evidence / "archives"
    archives.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="snapshot-", dir=archives) as directory:
        stage = Path(directory)
        files = [(evidence / "reports" / f"{day}.{ext}", f"reports/{day}.{ext}") for ext in ("json", "md")]
        files += [(p, f"ledgers/{p.name}") for p in sorted(evidence.glob("ledger*.json"))]
        files += [(root / "logs" / name, f"logs/{name}") for name in (
            f"preflight_{day}.log", f"automation_{day}.log", f"session_{day}.log",
            f"session_{day}.json", f"session_{day}.exitcode", f"shadow_report_{day}.txt",
            f"AUTOMATION_FAILURE_{day}.txt", f"watchdog_{day}.log")]
        missing = []
        for source, relative in files:
            if not source.exists():
                missing.append(relative)
                continue
            if source.is_symlink():
                raise ValueError("Evidence symlinks require manual review")
            destination = stage / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        # Daily quote evidence is essential. Do not copy the growing full history daily.
        for name in ("shadow_book.jsonl", "market_data_comparisons.jsonl"):
            source = evidence / name
            if source.is_symlink():
                raise ValueError("Evidence symlinks require manual review")
            if not source.exists():
                missing.append(name)
                continue
            with source.open() as incoming, (stage / name).open("w") as outgoing:
                for line in incoming:
                    if not line.strip():
                        continue
                    row = json.loads(line)  # Invalid evidence aborts; never silently discard it.
                    stamp = row.get("ts") or row.get("observed_at") or row.get("session_date") or ""
                    from app.evaluation.shadow_summary import session_date
                    row_day = session_date({"ts": stamp}) or str(stamp)[:10]
                    if row_day == day:
                        outgoing.write(line if line.endswith("\n") else line + "\n")
        hashes = {str(p.relative_to(stage)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sorted(stage.rglob("*")) if p.is_file()}
        if not hashes:
            raise ValueError("No session evidence to archive")
        manifest = {"schema_version": 1, "date": day,
                    "snapshot_code_commit": git(root, "rev-parse", "HEAD").stdout.strip(),
                    "files_sha256": hashes, "missing_files": missing,
                    "note": "Snapshot commit is not proof of session execution. See report/session context."}
        digest = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()[:16]
        write_json(stage / "manifest.json", manifest)
        destination = archives / day / digest
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(stage, destination)
        else:
            verify_archive(destination)
        return destination


def verify_archive(snapshot: Path):
    manifest = json.loads((snapshot / "manifest.json").read_text())
    expected = set(manifest["files_sha256"]) | {"manifest.json"}
    actual = {str(p.relative_to(snapshot)) for p in snapshot.rglob("*") if p.is_file()}
    if actual != expected:
        raise ValueError("Archive contents changed")
    for name, digest in manifest["files_sha256"].items():
        path = snapshot / name
        if path.is_symlink() or snapshot.resolve() not in path.resolve().parents:
            raise ValueError("Invalid archive path")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("Archive checksum mismatch")


def publish(root: Path, evidence: Path, checkout: Path, day: str) -> Path:
    """Push only session-artifacts. Remote races stop and can be retried safely."""
    outside_code(root, checkout)
    outside_code(root, evidence)
    if checkout.resolve() == evidence.resolve() or checkout.resolve() in evidence.resolve().parents or evidence.resolve() in checkout.resolve().parents:
        raise ValueError("Artifact checkout and evidence archive must be separate directories")
    snapshot = archive(root, evidence, day)  # Durable local evidence exists before network work.
    remote = git(root, "remote", "get-url", "origin").stdout.strip()
    if not (checkout / ".git").exists():
        if checkout.exists() and any(checkout.iterdir()):
            raise ValueError("Artifact checkout directory must be empty")
        checkout.mkdir(parents=True, exist_ok=True)
        git(checkout, "init", "--initial-branch=" + ARTIFACT_BRANCH)
        git(checkout, "remote", "add", "origin", remote)
        git(checkout, "config", "user.name", "Trader evidence publisher")
        git(checkout, "config", "user.email", "trader-evidence@localhost")
    if git(checkout, "remote", "get-url", "origin").stdout.strip() != remote:
        raise ValueError("Artifact remote differs from code remote")
    if git(checkout, "symbolic-ref", "--short", "HEAD").stdout.strip() != ARTIFACT_BRANCH:
        raise ValueError("Unexpected artifact branch")
    if git(checkout, "status", "--porcelain").stdout.strip():
        raise ValueError("Artifact checkout is dirty; local archive is preserved")
    remote_state = git(checkout, "ls-remote", "--exit-code", "--heads", "origin", ARTIFACT_BRANCH, check=False)
    if remote_state.returncode == 0:
        git(checkout, "fetch", "origin", f"refs/heads/{ARTIFACT_BRANCH}:refs/remotes/origin/{ARTIFACT_BRANCH}")
        has_head = git(checkout, "rev-parse", "--verify", "HEAD", check=False).returncode == 0
        if has_head:
            git(checkout, "merge", "--ff-only", f"origin/{ARTIFACT_BRANCH}")
        else:
            git(checkout, "checkout", "-B", ARTIFACT_BRANCH, f"origin/{ARTIFACT_BRANCH}")
    elif remote_state.returncode != 2:
        raise RuntimeError("Artifact remote unavailable")
    relative = Path("sessions") / day / snapshot.name
    destination = checkout / relative
    if destination.exists():
        verify_archive(destination)
        if (destination / "manifest.json").read_bytes() != (snapshot / "manifest.json").read_bytes():
            raise ValueError("Conflicting immutable snapshot")
    else:
        shutil.copytree(snapshot, destination)
        git(checkout, "add", "--", str(relative))
        git(checkout, "commit", "-m", f"Archive session evidence {day} {snapshot.name}")
    git(checkout, "push", "origin", f"HEAD:refs/heads/{ARTIFACT_BRANCH}")
    return snapshot
