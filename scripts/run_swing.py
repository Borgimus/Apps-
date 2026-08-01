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

    # Build the paper broker + data clients when credentials are present. Without them the
    # process still starts (health only) so the dashboard/readiness plumbing can be inspected.
    key_id = os.environ.get("SWING_ALPACA_KEY_ID")
    secret = os.environ.get("SWING_ALPACA_SECRET_KEY")
    broker_client = None
    if key_id and secret:
        from src.broker.alpaca_client import AlpacaPaperRESTClient
        broker_client = AlpacaPaperRESTClient(base_url, key_id, secret)  # paper endpoint enforced

    # Startup reconciliation against real broker truth when a client exists. Broker state wins;
    # any unknown/mismatched position blocks readiness (fail-closed) until resolved.
    def reconcile_fn() -> ReconResult:
        if broker_client is None:
            return ReconResult()  # no client -> nothing to reconcile (health-only start)
        from src.live.service import reconcile_live
        # Expected positions come from the persisted trade state; empty here means any live
        # position is surfaced as UNKNOWN_POSITION and blocks new risk until reconciled.
        return reconcile_live(broker_client, expected_positions=[], known_client_order_ids=set())

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

    # Optional trading loop. Opt-in via SWING_ENABLE_LOOP=1 and requires broker + data clients.
    # Mode gating is strict: only PAPER_AUTO submits; SHADOW/PAPER_CONFIRM only propose. Refuse to
    # run PAPER_AUTO unless the operator has explicitly acknowledged the acceptance gates.
    if os.environ.get("SWING_ENABLE_LOOP") == "1" and broker_client is not None:
        _start_loop(cfg, svc, broker_client, key_id, secret, base_url, logger)

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


def _start_loop(cfg, svc, broker_client, key_id, secret, base_url, logger) -> None:
    """Assemble and start the orchestrator tick loop in a background thread.

    SHADOW by default. PAPER_AUTO is refused unless SWING_ACCEPT_PAPER_AUTO=1 is set (the operator
    attesting the acceptance gates in docs/implementation_plan.md are met). Entry candidates are
    read from a scanner-produced setups file; managing existing positions requires the persisted
    trade-state store (documented next increment), so this loop currently proposes/acts on entries.
    """
    import threading

    from src.broker.alpaca_paper import AlpacaPaperBroker
    from src.broker.retry import RetryPolicy
    from src.data.alpaca_data import AlpacaDataClient
    from src.live.orchestrator import GateFlags, LiveOrchestrator, LoopDeps
    from src.live.service import (
        build_market_view,
        load_setups_file,
        reconcile_live,
        run_tick_loop,
    )
    from src.live.trade_state import TradeStateStore

    mode = cfg.mode
    if mode == "PAPER_AUTO" and os.environ.get("SWING_ACCEPT_PAPER_AUTO") != "1":
        logger.error("PAPER_AUTO requires SWING_ACCEPT_PAPER_AUTO=1 — refusing; running SHADOW")
        mode = "SHADOW"

    broker = AlpacaPaperBroker(base_url, key_id, secret, broker_client)
    feed = os.environ.get("SWING_ALPACA_DATA_FEED", "iex")
    data_client = AlpacaDataClient(key_id, secret, feed=feed)
    setups_path = os.environ.get("SWING_SETUPS_FILE", "./data/live_setups.json")
    store = TradeStateStore(os.environ.get("SWING_TRADE_STATE_FILE", "./data/live_trade_state.json"))

    from datetime import datetime, timezone

    def _reconcile():
        # Broker wins; expected positions + known order ids come from the durable trade store.
        return reconcile_live(broker_client, expected_positions=store.expected_positions(),
                              known_client_order_ids=store.known_client_order_ids())

    deps = LoopDeps(
        broker=broker,
        reconcile=_reconcile,
        get_positions=lambda: store.reconstruct_positions(broker.list_positions()),
        get_candidates=lambda: load_setups_file(setups_path),
        get_market=lambda sym: build_market_view(data_client, sym, config=cfg.as_dict(),
                                                 now=datetime.now(timezone.utc)),
        notifier=_LogNotifier(logger),
        config=cfg.as_dict(),
        mode=mode,
        now=datetime.now(timezone.utc),
        flags=GateFlags(paper_verified=svc.readiness.paper_endpoint_verified),
        retry=RetryPolicy(),
        sleep=lambda s: None,
        store=store,
    )
    orch = LiveOrchestrator(deps)
    interval = float(os.environ.get("SWING_TICK_SECONDS", "30"))

    def _loop():
        import time
        run_tick_loop(orch, interval_seconds=interval, sleep=time.sleep,
                      should_continue=lambda: svc.shutdown.accepting_new_entries(),
                      on_report=lambda r: logger.info("tick", extra={"mode": r.mode,
                          "submitted": len(r.submitted), "proposals": len(r.proposals),
                          "blockers": len(r.blockers)}))

    threading.Thread(target=_loop, daemon=True, name="swing-loop").start()
    logger.info("trading loop started", extra={"mode": mode, "interval_s": interval})


class _LogNotifier:
    """Minimal Notifier that logs redacted payloads (used when no external notifier is configured)."""

    def __init__(self, logger):
        self._logger = logger

    def send(self, notification) -> None:
        self._logger.info("notify", extra={"payload": notification.safe_payload()})


if __name__ == "__main__":
    raise SystemExit(main())
