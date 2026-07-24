"""
/sessions endpoints — start, stop, restart, and status for the supervised session runner.

Endpoints:
  POST /sessions/start    — start session_runner.py under supervisor control
  GET  /sessions/status   — rich snapshot: runner state, broker positions, validity
  POST /sessions/stop     — graceful stop (SIGTERM → SIGKILL after 15s)
  POST /sessions/restart  — stop + broker pre-check + start
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .supervisor import _supervisor

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("/start")
async def sessions_start():
    """
    Start session_runner.py under supervisor control.

    Refuses if: runner already active, LIVE_TRADING_ENABLED=true,
    or broker has unreconciled open orders.
    """
    try:
        return await _supervisor.start()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Start failed: {exc}")


@router.get("/status")
async def sessions_status():
    """
    Rich snapshot of supervisor and runner state.

    Returns runner_alive, pid, current_cycle, last_log_timestamp,
    broker_positions, broker_orders, account_equity, scanner_status,
    yfinance_data_status, last_error, and session_validity_state.
    Broker fields are queried live on each call.
    """
    try:
        return await _supervisor.get_status()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Status failed: {exc}")


@router.post("/stop")
async def sessions_stop():
    """
    Gracefully stop the current session_runner (SIGTERM, 15s timeout, then SIGKILL).

    Safe to call when no runner is active — returns a note instead of an error.
    """
    try:
        return await _supervisor.stop()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Stop failed: {exc}")


@router.post("/restart")
async def sessions_restart():
    """
    Stop the current runner, re-check broker state, then start fresh.

    Refuses if broker has unreconciled open orders after the stop.
    """
    try:
        return await _supervisor.restart()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Restart failed: {exc}")
