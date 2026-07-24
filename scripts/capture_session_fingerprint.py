"""Capture and verify the Phase 3 session fingerprint.

Outputs a JSON object recording the current runtime identity: commit SHA,
config hash, requirements.lock hash, universe hash, and broker adapter
version strings. Run before each Phase 3 session; compare against the
frozen baseline in evaluation/phase3_tracking.json::phase3_fingerprint.

Runtime output under ``logs/`` is intentionally excluded from the dirty-tree
check. Source, configuration, dependency, universe, and all other repository
changes remain blocking.

Usage:
    python scripts/capture_session_fingerprint.py
    python scripts/capture_session_fingerprint.py --verify
    python scripts/capture_session_fingerprint.py --verify --check-broker
    python scripts/capture_session_fingerprint.py --verify --baseline evaluation/phase3_tracking.json

Exit code 0 = all checks pass (or no --verify flag).
Exit code 1 = one or more checks fail; details printed to stderr.
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
_RUNTIME_PREFIXES = ("logs/",)


def _sha256_file(path: Path, truncate: int = 16) -> str:
    """Return first ``truncate`` hex chars of a file's SHA-256 digest."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:truncate]
    except FileNotFoundError:
        return "FILE_NOT_FOUND"


def _git_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=_REPO_ROOT,
            timeout=5,
        )
        return (
            result.stdout.strip()
            if result.returncode == 0
            else "GIT_UNAVAILABLE"
        )
    except Exception:
        return "GIT_UNAVAILABLE"


def _normalize_status_path(raw_path: str) -> str:
    """Normalize one path from ``git status --porcelain`` output."""
    path = raw_path.strip()
    if " -> " in path:
        path = path.rsplit(" -> ", 1)[1]
    if len(path) >= 2 and path[0] == path[-1] == '"':
        path = path[1:-1]
    return path.replace("\\", "/")


def _git_changed_paths() -> list[str]:
    """Return tracked and untracked paths reported by Git."""
    try:
        result = subprocess.run(
            [
                "git",
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ],
            capture_output=True,
            text=True,
            cwd=_REPO_ROOT,
            timeout=5,
        )
        if result.returncode != 0:
            return ["GIT_STATUS_UNAVAILABLE"]
        paths: list[str] = []
        for line in result.stdout.splitlines():
            if not line:
                continue
            # Porcelain v1 reserves the first two columns for XY status and the
            # third for a separator. Everything after that is the path.
            path = _normalize_status_path(line[3:] if len(line) > 3 else line)
            if path:
                paths.append(path)
        return paths
    except Exception:
        return ["GIT_STATUS_UNAVAILABLE"]


def _is_runtime_path(path: str) -> bool:
    """True only for known generated runtime output paths."""
    normalized = path.replace("\\", "/").lstrip("./")
    return any(
        normalized == prefix.rstrip("/") or normalized.startswith(prefix)
        for prefix in _RUNTIME_PREFIXES
    )


def _source_changes() -> list[str]:
    return [
        path
        for path in _git_changed_paths()
        if not _is_runtime_path(path)
    ]


def _ignored_runtime_changes() -> list[str]:
    return [
        path
        for path in _git_changed_paths()
        if _is_runtime_path(path)
    ]


def _git_dirty() -> bool:
    """True when non-runtime repository content has uncommitted changes."""
    return bool(_source_changes())


def _broker_adapter_version(module_path: str) -> str:
    """Return first 8 hex chars of a broker adapter source-file hash."""
    return _sha256_file(_REPO_ROOT / module_path, truncate=8)


def capture() -> dict:
    changed_paths = _git_changed_paths()
    source_changes = [
        path for path in changed_paths if not _is_runtime_path(path)
    ]
    ignored_runtime = [
        path for path in changed_paths if _is_runtime_path(path)
    ]

    broker_adapters = {
        "alpaca_broker": _broker_adapter_version(
            "app/brokers/alpaca_broker.py"
        ),
        "paper_broker": _broker_adapter_version(
            "app/brokers/paper_broker.py"
        ),
        "broker_interface": _broker_adapter_version(
            "app/brokers/broker_interface.py"
        ),
    }

    return {
        "commit_sha": _git_sha(),
        "working_tree_dirty": bool(source_changes),
        "source_changes": source_changes,
        "ignored_runtime_changes": ignored_runtime,
        "config_hash_sha256_16": _sha256_file(
            _REPO_ROOT / "config.yaml"
        ),
        "requirements_lock_hash_sha256_16": _sha256_file(
            _REPO_ROOT / "requirements.lock"
        ),
        "ticker_universe_hash_sha256_16": _sha256_file(
            _REPO_ROOT / "config" / "ticker_universe.yaml"
        ),
        "broker_adapter_hashes_sha256_8": broker_adapters,
    }


def _static_issues(baseline: dict, current: dict) -> list[str]:
    """Return descriptions for static fingerprint divergences."""
    issues: list[str] = []

    identifier = baseline.get("paper_account_identifier", "")
    if not identifier or "RECORD_" in identifier:
        issues.append(
            f"paper_account_identifier is not set: {identifier!r}. "
            "Record the non-sensitive account identifier before the first "
            "session. Use 'alpaca-paper-<last4>' or "
            "sha256(account_number)[:12]."
        )

    if current.get("working_tree_dirty"):
        changed = current.get("source_changes") or []
        detail = ", ".join(changed[:10]) or "unknown paths"
        issues.append(
            "working_tree_dirty=True — commit or stash non-runtime changes "
            f"before running a session: {detail}"
        )

    frozen_sha = baseline.get("commit_sha", "")
    current_sha = current.get("commit_sha", "")
    if (
        frozen_sha
        and frozen_sha not in ("CAPTURE_AFTER_PUSH", "")
        and frozen_sha != current_sha
    ):
        issues.append(
            f"commit_sha mismatch: baseline={frozen_sha[:12]!r} "
            f"current={current_sha[:12]!r} — HEAD has moved past the "
            "Phase 3 readiness commit. Declare a protocol amendment and "
            "update the baseline before counting this session."
        )

    hash_keys = [
        "config_hash_sha256_16",
        "requirements_lock_hash_sha256_16",
        "ticker_universe_hash_sha256_16",
    ]
    for key in hash_keys:
        baseline_value = baseline.get(key, "")
        current_value = current.get(key, "")
        if baseline_value and baseline_value != current_value:
            issues.append(
                f"{key}: baseline={baseline_value!r} "
                f"current={current_value!r}"
            )

    for adapter, baseline_hash in baseline.get(
        "broker_adapter_hashes_sha256_8",
        {},
    ).items():
        current_hash = current.get(
            "broker_adapter_hashes_sha256_8",
            {},
        ).get(adapter)
        if baseline_hash and baseline_hash != current_hash:
            issues.append(
                f"broker_adapter_hashes[{adapter}]: "
                f"baseline={baseline_hash!r} current={current_hash!r}"
            )

    return issues


async def _broker_issues() -> list[str]:
    """Connect to the configured broker and return blocking start issues."""
    issues: list[str] = []

    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))

    try:
        from app.brokers.factory import get_broker
        from app.config import get_settings
    except ImportError as exc:
        issues.append(
            f"Cannot import broker factory: {exc} — ensure app is installed"
        )
        return issues

    try:
        settings = get_settings()
        broker = get_broker(settings)
    except Exception as exc:
        issues.append(f"Cannot construct broker: {exc}")
        return issues

    try:
        ok, reason = broker.verify_paper_endpoint()
        if not ok:
            issues.append(
                f"Broker paper endpoint check failed: {reason}"
            )
            return issues
    except Exception as exc:
        issues.append(f"verify_paper_endpoint raised: {exc}")
        return issues

    try:
        from app.brokers.broker_interface import OrderStatus

        orders = await broker.get_orders(limit=200)
        working = {
            OrderStatus.NEW,
            OrderStatus.OPEN,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.ACCEPTED,
            OrderStatus.PENDING_NEW,
            OrderStatus.HELD,
        }
        open_orders = [
            order for order in orders if order.status in working
        ]
        if open_orders:
            ids = ", ".join(
                order.order_id[:8] for order in open_orders[:5]
            )
            issues.append(
                f"Broker has {len(open_orders)} open order(s) — cancel or "
                "wait for them to resolve before starting. "
                f"Order IDs (first 5): {ids}"
            )
    except NotImplementedError:
        pass
    except Exception as exc:
        issues.append(f"get_orders() failed: {exc}")

    try:
        positions = await broker.get_positions()
        option_positions = [
            position
            for position in positions
            if position.is_option and position.option_symbol
        ]
        if option_positions:
            symbols = ", ".join(
                position.option_symbol
                for position in option_positions[:5]
            )
            issues.append(
                f"Broker has {len(option_positions)} open option "
                "position(s) — close or roll them before starting. "
                f"Symbols (first 5): {symbols}"
            )
    except NotImplementedError:
        pass
    except Exception as exc:
        issues.append(f"get_positions() failed: {exc}")

    try:
        await broker.close()
    except Exception:
        pass

    return issues


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Run static checks; exit 1 if any fail",
    )
    parser.add_argument(
        "--check-broker",
        action="store_true",
        help=(
            "Also connect to the broker and check open orders/positions "
            "(requires credentials)"
        ),
    )
    parser.add_argument(
        "--baseline",
        default=str(
            _REPO_ROOT / "evaluation" / "phase3_tracking.json"
        ),
        help=(
            "Path to phase3_tracking.json containing "
            "phase3_fingerprint block"
        ),
    )
    args = parser.parse_args()

    fingerprint = capture()
    print(json.dumps(fingerprint, indent=2))

    if not args.verify and not args.check_broker:
        return

    all_issues: list[str] = []

    if args.verify:
        try:
            tracking = json.loads(Path(args.baseline).read_text())
            baseline = tracking.get("phase3_fingerprint", {})
        except Exception as exc:
            print(
                f"ERROR: could not read baseline from {args.baseline}: {exc}",
                file=sys.stderr,
            )
            sys.exit(1)
        all_issues.extend(_static_issues(baseline, fingerprint))

    if args.check_broker:
        all_issues.extend(asyncio.run(_broker_issues()))

    if all_issues:
        print(
            "\nPRE-SESSION GATE FAILED — session is NOT eligible for "
            "Phase 3 cohort:",
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
            "\nTo proceed: fix all issues above, or declare a protocol "
            "amendment and update the baseline in "
            "evaluation/phase3_tracking.json.",
            file=sys.stderr,
        )
        sys.exit(1)

    checks_run: list[str] = []
    if args.verify:
        checks_run.append("static")
    if args.check_broker:
        checks_run.append("broker")
    print(
        f"\nAll {'+'.join(checks_run)} checks passed — session is eligible "
        "for Phase 3 cohort.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
