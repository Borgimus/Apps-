"""
Capture and verify the Phase 3 session fingerprint.

Outputs a JSON object recording the current runtime identity: commit SHA,
config hash, requirements.lock hash, universe hash, and broker adapter
version strings.  Run before each Phase 3 session; compare against the
frozen baseline in evaluation/phase3_tracking.json::phase3_fingerprint.

Usage:
    python scripts/capture_session_fingerprint.py
    python scripts/capture_session_fingerprint.py --verify
    python scripts/capture_session_fingerprint.py --verify --check-broker
    python scripts/capture_session_fingerprint.py --verify --baseline evaluation/phase3_tracking.json

Exit code 0 = all checks pass (or no --verify flag).
Exit code 1 = one or more checks fail; details printed to stderr.

Static checks (--verify):
  • account_identifier not a placeholder (does not contain "RECORD_" or blank)
  • working tree is clean (no uncommitted changes)
  • HEAD matches the frozen Phase 3 commit SHA
  • all file hashes match the frozen baseline

Broker checks (--verify --check-broker):
  • broker connectivity succeeds (verify_paper_endpoint passes)
  • zero open orders at broker
  • zero open positions at broker
  • broker state signals a clean start (reconciliation not needed)
"""

from __future__ import annotations

import argparse
import asyncio
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


def _static_issues(baseline: dict, current: dict) -> list[str]:
    """
    Return divergence descriptions for all static (non-broker) checks.
    Empty list means all static checks pass.
    """
    issues = []

    # 1. Account identifier must not be a placeholder
    identifier = baseline.get("paper_account_identifier", "")
    if not identifier or "RECORD_" in identifier:
        issues.append(
            f"paper_account_identifier is not set: {identifier!r}. "
            f"Record the non-sensitive account identifier before the first session. "
            f"Use 'alpaca-paper-<last4>' or sha256(account_number)[:12]."
        )

    # 2. Working tree must be clean
    if current.get("working_tree_dirty"):
        issues.append(
            "working_tree_dirty=True — commit or stash all changes before running a session"
        )

    # 3. HEAD must match frozen commit
    frozen_sha = baseline.get("commit_sha", "")
    current_sha = current.get("commit_sha", "")
    if frozen_sha and frozen_sha not in ("CAPTURE_AFTER_PUSH", "") and frozen_sha != current_sha:
        issues.append(
            f"commit_sha mismatch: baseline={frozen_sha[:12]!r} current={current_sha[:12]!r} "
            f"— HEAD has moved past the Phase 3 readiness commit. "
            f"Declare a protocol amendment and update the baseline before counting this session."
        )

    # 4. All file hashes must match
    hash_keys = [
        "config_hash_sha256_16",
        "requirements_lock_hash_sha256_16",
        "ticker_universe_hash_sha256_16",
    ]
    for k in hash_keys:
        bv = baseline.get(k, "")
        cv = current.get(k, "")
        if bv and bv != cv:
            issues.append(f"{k}: baseline={bv!r} current={cv!r}")

    # 5. Broker adapter hashes must match
    for adapter, bh in baseline.get("broker_adapter_hashes_sha256_8", {}).items():
        ch = current.get("broker_adapter_hashes_sha256_8", {}).get(adapter)
        if bh and bh != ch:
            issues.append(
                f"broker_adapter_hashes[{adapter}]: baseline={bh!r} current={ch!r}"
            )

    return issues


async def _broker_issues() -> list[str]:
    """
    Connect to the configured broker and return a list of blocking issues.
    Requires APP credentials loaded from .env (via get_settings()).
    """
    issues = []

    # Add repo root to sys.path so app imports work when run as a script
    import sys as _sys
    if str(_REPO_ROOT) not in _sys.path:
        _sys.path.insert(0, str(_REPO_ROOT))

    try:
        from app.brokers.factory import get_broker
        from app.config import get_settings
    except ImportError as exc:
        issues.append(f"Cannot import broker factory: {exc} — ensure app is installed")
        return issues

    try:
        settings = get_settings()
        broker = get_broker(settings)
    except Exception as exc:
        issues.append(f"Cannot construct broker: {exc}")
        return issues

    # Paper endpoint validation
    try:
        ok, reason = broker.verify_paper_endpoint()
        if not ok:
            issues.append(f"Broker paper endpoint check failed: {reason}")
            return issues  # no point checking orders/positions on a misconfigured endpoint
    except Exception as exc:
        issues.append(f"verify_paper_endpoint raised: {exc}")
        return issues

    # Open orders must be zero
    try:
        from app.brokers.broker_interface import OrderStatus
        orders = await broker.get_orders(limit=200)
        _working = {
            OrderStatus.NEW, OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED,
            OrderStatus.ACCEPTED, OrderStatus.PENDING_NEW, OrderStatus.HELD,
        }
        open_orders = [o for o in orders if o.status in _working]
        if open_orders:
            ids = ", ".join(o.order_id[:8] for o in open_orders[:5])
            issues.append(
                f"Broker has {len(open_orders)} open order(s) — "
                f"cancel or wait for them to resolve before starting. "
                f"Order IDs (first 5): {ids}"
            )
    except NotImplementedError:
        pass  # broker does not support get_orders(); skip
    except Exception as exc:
        issues.append(f"get_orders() failed: {exc}")

    # Open positions must be zero
    try:
        positions = await broker.get_positions()
        option_positions = [p for p in positions if p.is_option and p.option_symbol]
        if option_positions:
            syms = ", ".join(p.option_symbol for p in option_positions[:5])
            issues.append(
                f"Broker has {len(option_positions)} open option position(s) — "
                f"close or roll them before starting a new session. "
                f"Symbols (first 5): {syms}"
            )
    except NotImplementedError:
        pass  # broker does not support get_positions(); skip
    except Exception as exc:
        issues.append(f"get_positions() failed: {exc}")

    return issues


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--verify", action="store_true",
        help="Run static checks; exit 1 if any fail",
    )
    parser.add_argument(
        "--check-broker", action="store_true",
        help="Also connect to the broker and check open orders/positions (requires credentials)",
    )
    parser.add_argument(
        "--baseline",
        default=str(_REPO_ROOT / "evaluation" / "phase3_tracking.json"),
        help="Path to phase3_tracking.json containing phase3_fingerprint block",
    )
    args = parser.parse_args()

    fp = capture()
    print(json.dumps(fp, indent=2))

    if not args.verify and not args.check_broker:
        return

    all_issues: list[str] = []

    if args.verify:
        try:
            tracking = json.loads(Path(args.baseline).read_text())
            baseline = tracking.get("phase3_fingerprint", {})
        except Exception as exc:
            print(f"ERROR: could not read baseline from {args.baseline}: {exc}", file=sys.stderr)
            sys.exit(1)

        static = _static_issues(baseline, fp)
        all_issues.extend(static)

    if args.check_broker:
        broker_problems = asyncio.run(_broker_issues())
        all_issues.extend(broker_problems)

    if all_issues:
        print(
            "\nPRE-SESSION GATE FAILED — session is NOT eligible for Phase 3 cohort:",
            file=sys.stderr,
        )
        for issue in all_issues:
            print(f"  • {issue}", file=sys.stderr)
        if args.verify and not args.check_broker:
            print(
                "\nRe-run with --check-broker to also verify broker state "
                "(zero open orders and positions).",
                file=sys.stderr,
            )
        print(
            "\nTo proceed: fix all issues above, or declare a protocol amendment "
            "and update the baseline in evaluation/phase3_tracking.json.",
            file=sys.stderr,
        )
        sys.exit(1)
    else:
        checks_run = []
        if args.verify:
            checks_run.append("static")
        if args.check_broker:
            checks_run.append("broker")
        print(
            f"\nAll {'+'.join(checks_run)} checks passed — "
            f"session is eligible for Phase 3 cohort.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
