"""
Capture and verify the Phase 3 session fingerprint.

Outputs a JSON object recording the current runtime identity: commit SHA,
config hash, requirements.lock hash, universe hash, and broker adapter
version strings.  Run before each Phase 3 session; compare against the
frozen baseline in evaluation/phase3_tracking.json::phase3_fingerprint.

Usage:
    python scripts/capture_session_fingerprint.py
    python scripts/capture_session_fingerprint.py --verify
    python scripts/capture_session_fingerprint.py --verify --baseline evaluation/phase3_tracking.json

Exit code 0 = fingerprint matches baseline (or no --verify flag).
Exit code 1 = fingerprint diverges from baseline; details printed to stderr.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _sha256_file(path: Path, truncate: int = 16) -> str:
    """Return first `truncate` hex chars of SHA-256 of file contents."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:truncate]
    except FileNotFoundError:
        return "FILE_NOT_FOUND"


def _git_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, cwd=_REPO_ROOT, timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else "GIT_UNAVAILABLE"
    except Exception:
        return "GIT_UNAVAILABLE"


def _git_dirty() -> bool:
    """True if working tree has uncommitted changes."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, cwd=_REPO_ROOT, timeout=5,
        )
        return bool(result.stdout.strip())
    except Exception:
        return False


def _broker_adapter_version(module_path: str) -> str:
    """Return first 8 hex chars of SHA-256 of a broker adapter source file."""
    p = _REPO_ROOT / module_path
    return _sha256_file(p, truncate=8)


def capture() -> dict:
    commit_sha = _git_sha()
    dirty = _git_dirty()

    config_hash = _sha256_file(_REPO_ROOT / "config.yaml")
    requirements_hash = _sha256_file(_REPO_ROOT / "requirements.lock")
    universe_hash = _sha256_file(_REPO_ROOT / "config" / "ticker_universe.yaml")

    broker_adapters = {
        "alpaca_broker": _broker_adapter_version("app/brokers/alpaca_broker.py"),
        "paper_broker": _broker_adapter_version("app/brokers/paper_broker.py"),
        "broker_interface": _broker_adapter_version("app/brokers/broker_interface.py"),
    }

    return {
        "commit_sha": commit_sha,
        "working_tree_dirty": dirty,
        "config_hash_sha256_16": config_hash,
        "requirements_lock_hash_sha256_16": requirements_hash,
        "ticker_universe_hash_sha256_16": universe_hash,
        "broker_adapter_hashes_sha256_8": broker_adapters,
    }


def _diverges(baseline: dict, current: dict) -> list[str]:
    """Return list of divergence descriptions; empty if fingerprints match."""
    issues = []
    keys = [
        "commit_sha",
        "config_hash_sha256_16",
        "requirements_lock_hash_sha256_16",
        "ticker_universe_hash_sha256_16",
    ]
    for k in keys:
        if baseline.get(k) and baseline[k] != current.get(k):
            issues.append(
                f"{k}: baseline={baseline[k]!r} current={current.get(k)!r}"
            )
    for adapter, bh in baseline.get("broker_adapter_hashes_sha256_8", {}).items():
        ch = current.get("broker_adapter_hashes_sha256_8", {}).get(adapter)
        if bh and bh != ch:
            issues.append(
                f"broker_adapter_hashes[{adapter}]: baseline={bh!r} current={ch!r}"
            )
    if current.get("working_tree_dirty"):
        issues.append("working_tree_dirty=True — uncommitted changes present")
    return issues


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--verify", action="store_true",
        help="Compare against frozen baseline; exit 1 if diverged",
    )
    parser.add_argument(
        "--baseline",
        default=str(_REPO_ROOT / "evaluation" / "phase3_tracking.json"),
        help="Path to phase3_tracking.json containing phase3_fingerprint block",
    )
    args = parser.parse_args()

    fp = capture()
    print(json.dumps(fp, indent=2))

    if args.verify:
        try:
            tracking = json.loads(Path(args.baseline).read_text())
            baseline = tracking.get("phase3_fingerprint", {})
        except Exception as exc:
            print(f"ERROR: could not read baseline from {args.baseline}: {exc}", file=sys.stderr)
            sys.exit(1)

        issues = _diverges(baseline, fp)
        if issues:
            print("\nFINGERPRINT DIVERGENCE — session is NOT eligible for Phase 3 cohort:", file=sys.stderr)
            for issue in issues:
                print(f"  • {issue}", file=sys.stderr)
            print(
                "\nTo proceed: fix the divergence, or declare a protocol amendment "
                "and update the baseline in evaluation/phase3_tracking.json.",
                file=sys.stderr,
            )
            sys.exit(1)
        else:
            print("\nFingerprint matches baseline — session is eligible for Phase 3 cohort.", file=sys.stderr)


if __name__ == "__main__":
    main()
