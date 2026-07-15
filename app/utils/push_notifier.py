"""
Push event notifier.

Writes compact structured events to logs/push_events.jsonl so that an
external monitor (Claude Code, webhook, etc.) can read and forward them
as push notifications.  Also maintains logs/live_status.json, a single
file overwritten every cycle with the latest session snapshot — used by
the /api/session/pulse API endpoint.

Events are append-only; the consumer marks them consumed via the 'consumed'
field by convention (the writer never deletes).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_DEFAULT_LOG_DIR = Path("logs")


class PushNotifier:
    """
    Writes push events and live status snapshots to the logs directory.

    Parameters
    ----------
    log_dir : Path | str
        Directory for push_events.jsonl and live_status.json.
    notify_interval_cycles : int
        Emit a heartbeat push event every N cycles.  Default 6 (30 min at
        5-min poll interval).
    """

    def __init__(
        self,
        log_dir: Path | str = _DEFAULT_LOG_DIR,
        notify_interval_cycles: int = 6,
    ) -> None:
        self._dir = Path(log_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._events_file = self._dir / "push_events.jsonl"
        self._status_file = self._dir / "live_status.json"
        self._interval = notify_interval_cycles
        # Session-local exit counters so on_session_end reports what was
        # actually notified, independent of risk-manager counter restoration.
        self._exit_count = 0
        self._win_count = 0

    # ── Public API ────────────────────────────────────────────────────────────

    def on_cycle(
        self,
        cycle: int,
        now: datetime,
        positions: int,
        entries_today: int,
        daily_pnl: float,
        unrealized_pnl: float,
        active_symbols: list,
        pending_orders: int,
        scanner_standby: bool,
        session_date: str,
    ) -> None:
        """Call once per cycle to update live_status.json and emit heartbeat events."""
        snapshot = {
            "ts": now.isoformat(),
            "session_date": session_date,
            "cycle": cycle,
            "positions": positions,
            "entries_today": entries_today,
            "pending_orders": pending_orders,
            "daily_pnl": round(daily_pnl, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "net_pnl": round(daily_pnl + unrealized_pnl, 2),
            "active_symbols": active_symbols,
            "scanner_standby": scanner_standby,
        }
        self._write_status(snapshot)

        if cycle % self._interval == 0:
            time_str = now.strftime("%H:%M ET")
            pnl_str = f"${daily_pnl:+.0f}" if daily_pnl != 0 else "$0"
            unreal_str = f" unreal={unrealized_pnl:+.0f}" if unrealized_pnl != 0 else ""
            syms = ",".join(active_symbols) if active_symbols else "none"
            msg = (
                f"[{time_str}] cycle={cycle} pos={positions} pnl={pnl_str}{unreal_str} "
                f"symbols={syms}"
            )
            self._emit("heartbeat", msg, snapshot)

    def on_fill(
        self,
        symbol: str,
        contract: str,
        direction: str,
        fill_price: float,
        quantity: int,
        strategy: str,
        now: datetime,
    ) -> None:
        """Emit an event when an entry order is confirmed filled."""
        time_str = now.strftime("%H:%M ET")
        dir_str = "LONG" if direction.upper() in ("LONG", "buy", "BUY_TO_OPEN") else "SHORT"
        msg = (
            f"FILL [{time_str}] {symbol} {dir_str} x{quantity} @ ${fill_price:.2f} "
            f"({strategy}) {contract}"
        )
        self._emit("fill", msg, {
            "symbol": symbol, "contract": contract, "direction": dir_str,
            "fill_price": fill_price, "quantity": quantity, "strategy": strategy,
            "ts": now.isoformat(),
        })

    def on_exit(
        self,
        symbol: str,
        contract: str,
        exit_price: float,
        pnl: float,
        reason: str,
        hold_secs: float,
        now: datetime,
    ) -> None:
        """Emit an event when a position is closed (broker-confirmed values)."""
        self._exit_count += 1
        if pnl > 0:
            self._win_count += 1
        time_str = now.strftime("%H:%M ET")
        hold_min = int(hold_secs // 60)
        pnl_str = f"${pnl:+.2f}"
        msg = (
            f"EXIT [{time_str}] {symbol} @ ${exit_price:.2f} → {pnl_str} "
            f"({reason}, {hold_min}m hold)"
        )
        self._emit("exit", msg, {
            "symbol": symbol, "contract": contract, "exit_price": exit_price,
            "pnl": pnl, "reason": reason, "hold_seconds": hold_secs,
            "ts": now.isoformat(),
        })

    def on_eod_warning(self, minutes_remaining: float, now: datetime) -> None:
        """Emit once when the EOD exit window is approaching."""
        time_str = now.strftime("%H:%M ET")
        msg = f"EOD WARNING [{time_str}] {minutes_remaining:.0f} min to EOD exit"
        self._emit("eod_warning", msg, {"minutes_remaining": minutes_remaining, "ts": now.isoformat()})

    def on_session_end(
        self,
        daily_pnl: float,
        now: datetime,
        trades: Optional[int] = None,
        wins: Optional[int] = None,
    ) -> None:
        """Emit a final summary event at session close.

        trades/wins default to the notifier's own exit counters, which track
        every on_exit emitted this process (counts only this segment after a
        mid-day restart)."""
        if trades is None:
            trades = self._exit_count
        if wins is None:
            wins = self._win_count
        time_str = now.strftime("%H:%M ET")
        wr = f"{wins}/{trades}" if trades > 0 else "0/0"
        pnl_str = f"${daily_pnl:+.2f}"
        msg = f"SESSION END [{time_str}] PnL={pnl_str} trades={trades} W/L={wr}"
        self._emit("session_end", msg, {
            "daily_pnl": daily_pnl, "trades": trades, "wins": wins,
            "ts": now.isoformat(),
        })
        # Clear live status so the API shows session is over
        self._write_status({"ts": now.isoformat(), "session_active": False, "daily_pnl": round(daily_pnl, 2)})

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _emit(self, event_type: str, message: str, data: Dict[str, Any]) -> None:
        entry = {
            "event": event_type,
            "message": message,
            "data": data,
            "consumed": False,
        }
        try:
            with self._events_file.open("a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as exc:
            logger.warning("PushNotifier: failed to write event %s: %s", event_type, exc)

    def _write_status(self, snapshot: Dict[str, Any]) -> None:
        try:
            tmp = self._status_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(snapshot, indent=2))
            tmp.replace(self._status_file)
        except Exception as exc:
            logger.warning("PushNotifier: failed to write live_status: %s", exc)
