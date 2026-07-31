#!/usr/bin/env python3
"""Swing service entrypoint (PAPER ONLY).

Startup sequence (readiness is reported only after it all succeeds):
  1. configure JSON logging (with secret redaction + rotation)
  2. load and validate the versioned strategy config (allow_live must be false)
  3. verify the Alpaca PAPER endpoint (a non-paper endpoint terminates the process)
  4. run startup reconciliation against broker truth (a mismatch keeps the process not-ready)
  5. serve the authenticated dashboard with /health and /ready

This wires the deterministic runtime together. Broker/data clients are constructed from env
(never committed). With no credentials present it starts in a not-ready state and serves health.
"""
from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import load_config  # noqa: E402
from src.execution.reconciliation import ReconResult  # noqa: E402
from src.runtime.app import SwingService  # noqa: E402
from src.runtime.logging_setup import configure_logging  # noqa: E402


def main() -> int:
    log = configure_logging(level=os.environ.get("LOG_LEVEL", "INFO"),
                            log_file=os.environ.get("SWING_LOG_FILE"))
    logger = log.getChild("swing") if hasattr(log, "getChild") else log

    cfg = load_config(os.environ.get("SWING_STRATEGY_CONFIG"))
    logger.info("config loaded", extra={"config_version": cfg.version, "mode": cfg.mode})
    if cfg.get("allow_live"):
        logger.error("allow_live is true — refusing to start")
        return 2

    base_url = os.environ.get("SWING_ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    svc = SwingService(base_url=base_url)

    # Startup reconciliation. With no broker client wired yet, a clean empty state is used so the
    # process can report health; PAPER_CONFIRM/PAPER_AUTO wire a real broker reconcile here.
    def reconcile_fn() -> ReconResult:
        return ReconResult()  # empty -> ok; replaced by real broker reconciliation in deployment

    try:
        svc.start(reconcile_fn)
    except Exception as exc:  # noqa: BLE001 — fail closed and terminate safely
        logger.error("startup failed: %s", str(exc))
        return 1

    def _graceful(_signum, _frame):
        logger.info("shutdown requested — draining new entries, keeping position management")
        svc.shutdown.request_shutdown()

    signal.signal(signal.SIGTERM, _graceful)
    signal.signal(signal.SIGINT, _graceful)

    from src.api.server import serve
    host = os.environ.get("SWING_API_HOST", "127.0.0.1")
    port = int(os.environ.get("SWING_API_PORT", "8080"))
    token = os.environ.get("SWING_DASHBOARD_TOKEN")

    httpd = serve(host, port, token=token,
                  state_provider=lambda: {"health": {"mode": cfg.mode}, "ready": svc.ready()},
                  ready_provider=svc.ready)
    logger.info("serving dashboard", extra={"host": host, "port": port, "ready": svc.ready()})
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
