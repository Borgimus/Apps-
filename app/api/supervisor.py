"""
Session Supervisor — manages session_runner.py as a supervised background process.

Responsibilities:
  - Start / stop / restart session_runner.py as a subprocess
  - Heartbeat every 60s: check liveness, parse logs, update DB snapshot
  - Detect stalled cycles (no log output in >120s)
  - Classify session validity state (VALID / STANDBY / INVALID_DATA /
    INFRA_INTERRUPTED / BROKER_RISK)
  - Enforce safety invariants: no duplicate runners, no blind restart

Safety guarantees:
  - Never starts a second runner if one is already alive
  - Checks broker for open orders before start/restart (open orders = unreconciled state)
  - Never auto-restarts — only explicit API calls trigger restarts
  - Refuses to start when LIVE_TRADING_ENABLED=true
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from zoneinfo import ZoneInfo

from .models import AsyncSessionLocal, DBSupervisorSession

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")
_LOG_STALL_SECS = 120   # runner is stalled if no log output for this long
_HEARTBEAT_INTERVAL = 60  # seconds between supervisor heartbeat ticks

# Log line patterns
_RE_CYCLE = re.compile(r"\[cycle\s+(\d+)\]")
_RE_NO_DAILY = re.compile(r"No daily data returned", re.IGNORECASE)
_RE_429 = re.compile(r"Too Many Requests|HTTP 429", re.IGNORECASE)
_RE_DATA_FETCH_ERR = re.compile(r"data_fetch_error", re.IGNORECASE)
_RE_ERROR = re.compile(r"\b(ERROR|CRITICAL)\b")

# Open order statuses per broker interface contract
_OPEN_ORDER_STATUSES = {"new", "partially_filled", "pending_new", "accepted", "held"}


class _SupervisorState:
    """In-process mutable state for the running session. Lives for the API process lifetime."""

    def __init__(self):
        self.process: Optional[asyncio.subprocess.Process] = None
        self.pid: Optional[int] = None
        self.start_time: Optional[datetime] = None
        self.current_cycle: int = 0
        self.last_log_line: Optional[str] = None
        self.last_log_timestamp: Optional[datetime] = None
        self.last_error: Optional[str] = None
        self.validity_state: Optional[str] = None
        self._no_daily_count: int = 0
        self._total_symbol_count: int = 38   # matches ticker_universe.yaml
        self._log_reader_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._lock: asyncio.Lock = asyncio.Lock()
        self.db_id: Optional[int] = None    # DBSupervisorSession.id for current run


_state = _SupervisorState()


# ── Background tasks ──────────────────────────────────────────────────────────

async def _read_logs(process: asyncio.subprocess.Process, state: _SupervisorState) -> None:
    """Read subprocess stdout line by line, update in-process state."""
    try:
        while True:
            line_bytes = await process.stdout.readline()
            if not line_bytes:
                break
            line = line_bytes.decode("utf-8", errors="replace").rstrip()
            if not line:
                continue

            state.last_log_line = line[-1000:]   # cap stored length
            state.last_log_timestamp = datetime.now(tz=_ET)

            m = _RE_CYCLE.search(line)
            if m:
                n = int(m.group(1))
                if n > state.current_cycle:
                    state.current_cycle = n

            if _RE_NO_DAILY.search(line):
                state._no_daily_count += 1

            if _RE_ERROR.search(line):
                state.last_error = line[-500:]

            logger.debug("[runner] %s", line)
    except Exception as exc:
        logger.warning("Supervisor log reader error: %s", exc)


async def _heartbeat_loop(broker, state: _SupervisorState) -> None:
    """Fire _do_heartbeat every _HEARTBEAT_INTERVAL seconds."""
    while True:
        await asyncio.sleep(_HEARTBEAT_INTERVAL)
        try:
            await _do_heartbeat(broker, state)
        except Exception as exc:
            logger.warning("Supervisor heartbeat error: %s", exc)


async def _do_heartbeat(broker, state: _SupervisorState) -> None:
    now = datetime.now(tz=_ET)

    runner_alive = state.process is not None and state.process.returncode is None

    # Stall detection
    if runner_alive and state.last_log_timestamp:
        age = (now - state.last_log_timestamp).total_seconds()
        if age > _LOG_STALL_SECS:
            logger.warning(
                "Session runner stalled — no log output for %ds (pid=%s)", int(age), state.pid
            )

    # Broker snapshot
    broker_positions: list = []
    broker_orders: list = []
    account_equity: Optional[float] = None
    if broker is not None:
        try:
            positions = await broker.get_positions()
            broker_positions = [
                {"symbol": p.symbol, "qty": p.quantity, "avg_cost": float(p.avg_cost)}
                for p in positions
            ]
            orders = await broker.get_orders(status="open", limit=20)
            broker_orders = [
                {"order_id": o.order_id, "symbol": o.symbol, "status": o.status}
                for o in orders
                if o.status in _OPEN_ORDER_STATUSES
            ]
            acct = await broker.get_account()
            account_equity = float(acct.equity)
        except Exception as exc:
            logger.warning("Supervisor heartbeat broker error: %s", exc)

    state.validity_state = _classify_validity(state, runner_alive, broker_positions, now)

    db_status = "running" if runner_alive else "stopped"

    if state.db_id is not None:
        broker_snap = json.dumps(
            {"positions": broker_positions, "orders": broker_orders,
             "equity": account_equity, "ts": now.isoformat()}
        )
        try:
            from sqlalchemy import select
            async with AsyncSessionLocal() as db:
                row = (
                    await db.execute(
                        select(DBSupervisorSession)
                        .where(DBSupervisorSession.id == state.db_id)
                    )
                ).scalars().first()
                if row:
                    row.status = db_status
                    row.pid = state.pid
                    row.last_heartbeat = now.replace(tzinfo=None)
                    row.last_cycle = state.current_cycle
                    row.last_log_line = state.last_log_line
                    row.last_error = state.last_error
                    row.broker_snapshot = broker_snap
                    row.validity_state = state.validity_state
                    await db.commit()
        except Exception as exc:
            logger.warning("Supervisor heartbeat DB write error: %s", exc)


def _classify_validity(
    state: _SupervisorState,
    runner_alive: bool,
    broker_positions: list,
    now: datetime,
) -> str:
    eod_reached = now.hour > 12 or (now.hour == 12 and now.minute >= 30)

    # INVALID_DATA: more than half the universe returned no daily data
    if state._no_daily_count > state._total_symbol_count * 0.5:
        return "INVALID_DATA"

    # INFRA_INTERRUPTED: died before EOD
    if not runner_alive and state.start_time and not eod_reached:
        return "INFRA_INTERRUPTED"

    # VALID: reached EOD cleanly
    if eod_reached and not runner_alive and state.start_time:
        return "VALID" if state._no_daily_count == 0 else "STANDBY"

    # Running or post-run default
    return "STANDBY"


# ── Supervisor ────────────────────────────────────────────────────────────────

class SessionSupervisor:
    """
    Singleton that owns session_runner.py as a supervised subprocess.

    Instantiated once at module import; broker reference injected by API startup.
    """

    def __init__(self) -> None:
        self._broker = None

    def set_broker(self, broker) -> None:
        self._broker = broker

    def is_running(self) -> bool:
        return _state.process is not None and _state.process.returncode is None

    # ── Public API ─────────────────────────────────────────────────────────

    async def start(self, date_str: Optional[str] = None) -> dict:
        """
        Start session_runner.py under supervisor control.

        Raises RuntimeError on:
          - runner already active
          - LIVE_TRADING_ENABLED=true
          - broker has unreconciled open orders
        """
        async with _state._lock:
            if self.is_running():
                raise RuntimeError(
                    f"Session runner is already active (pid={_state.pid})"
                )

            from ..config import get_settings
            if get_settings().live_trading_enabled:
                raise RuntimeError(
                    "LIVE_TRADING_ENABLED=true — supervisor refuses to start"
                )

            await self._broker_pre_check()

            today = date_str or str(date.today())
            self._reset_state()

            runner_script = str(
                Path(__file__).parents[2] / "scripts" / "session_runner.py"
            )
            cmd = [
                sys.executable, "-u", runner_script,
                "--poll", "30",
                "--reconcile-interval", "10",
            ]

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env={**os.environ},
            )

            _state.process = process
            _state.pid = process.pid
            _state.start_time = datetime.now(tz=_ET)

            _state._log_reader_task = asyncio.create_task(
                _read_logs(process, _state), name="supervisor-log-reader"
            )
            _state._heartbeat_task = asyncio.create_task(
                _heartbeat_loop(self._broker, _state), name="supervisor-heartbeat"
            )

            now = _state.start_time
            async with AsyncSessionLocal() as db:
                row = DBSupervisorSession(
                    session_date=today,
                    pid=process.pid,
                    status="running",
                    start_time=now.replace(tzinfo=None),
                    last_heartbeat=now.replace(tzinfo=None),
                    validity_state="STANDBY",
                )
                db.add(row)
                await db.commit()
                await db.refresh(row)
                _state.db_id = row.id

            logger.info(
                "Supervisor started session_runner.py — pid=%d session_date=%s db_id=%s",
                process.pid, today, _state.db_id,
            )
            return {"started": True, "pid": process.pid, "session_date": today, "db_id": _state.db_id}

    async def stop(self) -> dict:
        """
        Send SIGTERM to the runner; wait up to 15s for graceful exit.
        Falls back to SIGKILL if the process does not exit in time.
        """
        async with _state._lock:
            if not self.is_running():
                return {"stopped": True, "note": "runner was not active"}

            pid = _state.pid
            process = _state.process

            try:
                process.send_signal(signal.SIGTERM)
                logger.info("Supervisor sent SIGTERM to pid=%d", pid)
            except ProcessLookupError:
                pass

            try:
                await asyncio.wait_for(process.wait(), timeout=15.0)
            except asyncio.TimeoutError:
                logger.warning("Runner pid=%d did not exit in 15s — sending SIGKILL", pid)
                try:
                    process.kill()
                    await process.wait()
                except ProcessLookupError:
                    pass

            await self._finalize_db_record("stopped")

            self._cancel_background_tasks()
            _state.process = None

            return {
                "stopped": True,
                "pid": pid,
                "validity_state": _state.validity_state,
            }

    async def restart(self) -> dict:
        """Stop current runner, re-check broker, start fresh."""
        stop_result = await self.stop()
        # Re-run broker pre-check after stop (state may have changed)
        await self._broker_pre_check()
        start_result = await self.start()
        return {"restarted": True, "stop": stop_result, "start": start_result}

    async def get_status(self) -> dict:
        """Return a rich status snapshot (queries broker live)."""
        runner_alive = self.is_running()

        broker_positions: list = []
        broker_orders: list = []
        account_equity: Optional[float] = None
        if self._broker is not None:
            try:
                positions = await self._broker.get_positions()
                broker_positions = [
                    {"symbol": p.symbol, "qty": p.quantity, "avg_cost": float(p.avg_cost)}
                    for p in positions
                ]
                orders = await self._broker.get_orders(status="open", limit=20)
                broker_orders = [
                    {"order_id": o.order_id, "symbol": o.symbol, "status": o.status}
                    for o in orders
                    if o.status in _OPEN_ORDER_STATUSES
                ]
                acct = await self._broker.get_account()
                account_equity = float(acct.equity)
            except Exception as exc:
                logger.warning("Supervisor status broker error: %s", exc)

        yfinance_data_status: Optional[str] = None
        if _state._no_daily_count > 0:
            yfinance_data_status = f"failure ({_state._no_daily_count} no-data lines seen)"
        elif _state.current_cycle > 0:
            yfinance_data_status = "ok"

        scanner_status: Optional[str] = None
        if _state.last_log_line:
            if "STANDBY" in _state.last_log_line.upper():
                scanner_status = "standby"
            elif _RE_ERROR.search(_state.last_log_line):
                scanner_status = "error"
            elif _state.current_cycle > 0:
                scanner_status = "running"

        last_log_ts = (
            _state.last_log_timestamp.isoformat() if _state.last_log_timestamp else None
        )

        return {
            "runner_alive": runner_alive,
            "pid": _state.pid if runner_alive else None,
            "current_cycle": _state.current_cycle,
            "last_log_timestamp": last_log_ts,
            "broker_positions": broker_positions,
            "broker_orders": broker_orders,
            "account_equity": account_equity,
            "scanner_status": scanner_status,
            "yahoo_cookie_status": None,
            "yfinance_data_status": yfinance_data_status,
            "last_error": _state.last_error,
            "session_validity_state": _state.validity_state,
        }

    # ── Internal helpers ───────────────────────────────────────────────────

    async def _broker_pre_check(self) -> None:
        """
        Refuse start/restart if broker has unreconciled open orders.

        Open orders alongside a fresh runner start risk duplicate fills.
        Positions alone are acceptable — session_runner's SessionRecovery
        will detect and re-link them.
        """
        if self._broker is None:
            return
        try:
            orders = await self._broker.get_orders(status="open", limit=10)
            open_orders = [o for o in orders if o.status in _OPEN_ORDER_STATUSES]
            if open_orders:
                symbols = ", ".join(o.symbol for o in open_orders[:5])
                raise RuntimeError(
                    f"Broker has {len(open_orders)} open order(s) ({symbols}). "
                    "Cancel or reconcile them before starting a new session."
                )
        except RuntimeError:
            raise
        except Exception as exc:
            logger.warning("Broker pre-check non-fatal error: %s", exc)

    def _reset_state(self) -> None:
        _state.current_cycle = 0
        _state.last_log_line = None
        _state.last_log_timestamp = None
        _state.last_error = None
        _state.validity_state = None
        _state._no_daily_count = 0
        _state.db_id = None

    def _cancel_background_tasks(self) -> None:
        for task in [_state._log_reader_task, _state._heartbeat_task]:
            if task and not task.done():
                task.cancel()
        _state._log_reader_task = None
        _state._heartbeat_task = None

    async def _finalize_db_record(self, final_status: str) -> None:
        if _state.db_id is None:
            return
        now = datetime.now(tz=_ET)
        try:
            from sqlalchemy import select
            async with AsyncSessionLocal() as db:
                row = (
                    await db.execute(
                        select(DBSupervisorSession)
                        .where(DBSupervisorSession.id == _state.db_id)
                    )
                ).scalars().first()
                if row:
                    row.status = final_status
                    row.stop_time = now.replace(tzinfo=None)
                    row.last_cycle = _state.current_cycle
                    row.last_log_line = _state.last_log_line
                    row.last_error = _state.last_error
                    row.validity_state = _state.validity_state or "INFRA_INTERRUPTED"
                    await db.commit()
        except Exception as exc:
            logger.warning("Supervisor DB finalize error: %s", exc)


# Module-level singleton
_supervisor = SessionSupervisor()
