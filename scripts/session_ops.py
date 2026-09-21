#!/usr/bin/env python3
"""Operations CLI. Watchdog and alerts work without a running session process."""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import os
import sys
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from app.operations.monitor import ET, calendar, notify, watchdog, verify_flat
from app.operations.artifacts import outside_code, publish, seed_evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["watchdog", "calendar", "publish", "migrate", "alert", "check-config", "test-alert", "verify-flat"])
    parser.add_argument("--date", default=str(datetime.now(ET).date()))
    parser.add_argument("--code", default="operations_failure")
    parser.add_argument("--message", default="Trader operations failed. Review host logs.")
    parser.add_argument("--source-evaluation", type=Path, help="Migration-only source, such as a verified pre-deployment backup")
    args = parser.parse_args()
    load_dotenv(os.getenv("TRADER_OPS_ENV", "/root/trader-ops.env"), override=False)
    load_dotenv(ROOT / ".env", override=False)
    evidence = Path(os.getenv("TRADER_EVIDENCE_DIR", "/root/trader-evidence"))
    checkout = Path(os.getenv("TRADER_ARTIFACT_CHECKOUT", "/root/trader-artifacts"))
    locks = Path(os.getenv("TRADER_LOCK_DIR", str(ROOT.parent)))
    try:
        with ExitStack() as stack:
            locks.mkdir(parents=True, exist_ok=True)
            lock = stack.enter_context((locks / ".session-operations.lock").open("a"))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.command in ("publish", "migrate"):
                session_lock = stack.enter_context((locks / ".session.lock").open("a"))
                fcntl.flock(session_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.command == "calendar":
                return 0 if calendar(ROOT, refresh=True) else 3
            if args.command == "verify-flat":
                asyncio.run(verify_flat())
                print("BROKER_FLAT: 0 positions, 0 open orders (paper account)")
                return 0
            if args.command == "watchdog":
                return 0 if watchdog(ROOT) else 1
            if args.command in ("alert", "test-alert"):
                code = args.code if args.command == "alert" else "delivery_test"
                return 0 if notify(ROOT, code, args.message) else 1
            if args.command == "migrate":
                seed_evidence(ROOT, evidence, source_evaluation=args.source_evaluation)
                print("EVIDENCE_MIGRATION_COMPLETE")
            elif args.command == "publish":
                destination = publish(ROOT, evidence, checkout, args.date)
                print(f"ARTIFACTS_PUBLISHED snapshot={destination.name}")
            elif args.command == "check-config":
                from urllib.parse import urlparse
                topic = urlparse(os.getenv("OPS_NTFY_URL", ""))
                if topic.scheme != "https" or not topic.hostname or topic.username or topic.password:
                    raise ValueError("Notification URL is not configured")
                outside_code(ROOT, evidence)
                if not (evidence / "migration.json").is_file():
                    raise ValueError("Historical evidence migration has not completed")
                print("OPERATIONS_CONFIG_READY (delivery must be tested separately)")
            return 0
    except BlockingIOError:
        print("STOP: another operation or trading session holds the lock", file=sys.stderr)
        return 75
    except Exception as exc:
        # Git stderr and HTTP errors can disclose credential-bearing URLs.
        print(f"OPERATIONS_FAILED command={args.command} type={type(exc).__name__}", file=sys.stderr)
        notify(ROOT, args.command.replace("-", "_") + "_failed",
               f"Trader {args.command} failed ({type(exc).__name__}). Check host logs. Local evidence is preserved.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
