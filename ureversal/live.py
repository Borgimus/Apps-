"""Real-time scanner and trading loop.

Pipeline: Alpaca trade websocket → per-second bar aggregation → the SAME
`SignalEngine` and `ExitEvaluator` the backtester uses → risk layer → venue.

Modes:
- scan  : signals only, no orders (real-time scanner deliverable)
- trade : orders via Alpaca paper unless live.mode=live AND
          LIVE_TRADING_ENABLED=true (§7 mode gate)

Every second the trader writes `status.json` and appends signal/trade events
to `events.jsonl` in the cache dir — the dashboard reads those files, so the
trading process never blocks on the UI.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .backtest import ExitEvaluator
from .config import Config
from .execution import ExecutionVenue, Fill, make_venue
from .risk import RiskManager
from .signals import SignalEngine, Trigger

log = logging.getLogger("ureversal.live")
ET = ZoneInfo("America/New_York")


class BarAggregator:
    """Buckets websocket trades into 1-second bars for one symbol."""

    def __init__(self, max_ffill_s: int):
        self.max_ffill_s = max_ffill_s
        self.buckets: dict[int, list[tuple[float, float]]] = {}
        self.last_close: float | None = None
        self.stale_s = 0

    def add_trade(self, ts_epoch_s: float, price: float, size: float) -> None:
        self.buckets.setdefault(int(ts_epoch_s), []).append((price, size))

    def close_second(self, sec: int) -> tuple[float | None, bool]:
        """(close, real) for the finished second; None if unfillable gap."""
        trades = self.buckets.pop(sec, [])
        # drop any stragglers older than the second being closed
        for k in [k for k in self.buckets if k < sec]:
            self.buckets.pop(k)
        if trades:
            self.last_close = trades[-1][0]
            self.stale_s = 0
            return self.last_close, True
        self.stale_s += 1
        if self.last_close is None or self.stale_s > self.max_ffill_s:
            return None, False
        return self.last_close, False


@dataclass
class OpenPosition:
    qty: int
    entry_px: float
    entry_ts: str
    entry_second: int
    evaluator: ExitEvaluator
    trigger_z: float


class LiveTrader:
    def __init__(self, cfg: Config, execute: bool, venue: ExecutionVenue | None = None):
        self.cfg = cfg
        self.execute = execute
        self.engine = SignalEngine(cfg)
        self.risk = RiskManager(cfg)
        self.venue = venue if venue is not None else (make_venue(cfg) if execute else None)
        self.agg = {cfg.target: BarAggregator(cfg.max_ffill_s),
                    cfg.leader: BarAggregator(cfg.max_ffill_s)}
        self.position: OpenPosition | None = None
        self.bar_index = -1
        self.done = False
        cfg.cache_dir.mkdir(parents=True, exist_ok=True)
        self.events_path = cfg.cache_dir / "events.jsonl"
        self.status_path = cfg.cache_dir / "status.json"

    # ── journaling ──

    def _event(self, kind: str, **kw) -> None:
        rec = {"ts": dt.datetime.now(ET).isoformat(), "kind": kind, **kw}
        with self.events_path.open("a") as f:
            f.write(json.dumps(rec) + "\n")

    def _status(self, now: dt.datetime, extra: dict | None = None) -> None:
        s = {
            "ts": now.isoformat(),
            "mode": ("live" if self.execute and getattr(self.venue, "is_live", False)
                     else "paper" if self.execute else "scan"),
            "state": self.engine.state.value,
            "position": asdict(self.position) if self.position else None,
            "risk": self.risk.state,
            **(extra or {}),
        }
        if s["position"]:
            s["position"].pop("evaluator", None)
        self.status_path.write_text(json.dumps(s, default=str, indent=1))

    # ── per-second evaluation ──

    def on_second(self, sec_epoch: int) -> None:
        now = dt.datetime.fromtimestamp(sec_epoch, ET)
        t_close, t_real = self.agg[self.cfg.target].close_second(sec_epoch)
        l_close, l_real = self.agg[self.cfg.leader].close_second(sec_epoch)
        if t_close is None or l_close is None:
            self._status(now, {"note": "waiting for prints"})
            return
        ts = pd.Timestamp(sec_epoch, unit="s", tz="UTC").tz_convert(ET)
        trigger = self.engine.update(ts, t_close, t_real, l_close, l_real)
        self.bar_index += 1

        if self.position:
            self._manage_exit(now, t_close)
        elif trigger is not None:
            self._maybe_enter(now, trigger, t_close)

        if now.time() >= self.cfg.hard_exit:
            if self.position:
                self._close_position(now, t_close, "hard_exit")
            self.done = True
        self._status(now)

    def _maybe_enter(self, now: dt.datetime, trig: Trigger, ref_px: float) -> None:
        equity = self.venue.equity() if self.execute else float(
            self.cfg.risk["account_equity_fallback"])
        decision = self.risk.can_enter(now, equity)
        self._event("trigger", z=trig.z, spy_flat_slope=trig.spy_flat_slope,
                    dia_rev_slope=trig.dia_rev_slope, allowed=decision.allowed,
                    reason=decision.reason)
        if not decision.allowed:
            log.info("trigger suppressed: %s", decision.reason)
            return
        qty = self.risk.position_size(equity, ref_px)
        if qty <= 0:
            return
        if self.execute:
            fill = self.venue.buy_marketable_limit(self.cfg.target, qty)
            if fill is None:
                self._event("entry_unfilled", qty=qty)
                return
            px, qty = fill.price, fill.qty
        else:
            px = ref_px  # scan mode: hypothetical
        self.risk.record_entry()
        self.position = OpenPosition(
            qty=qty, entry_px=px, entry_ts=now.isoformat(),
            entry_second=self.bar_index,
            evaluator=ExitEvaluator(self.cfg.exits, self.cfg.signals, px),
            trigger_z=trig.z,
        )
        self._event("entry", qty=qty, px=px, z=trig.z)
        log.info("ENTER long %d %s @ %.2f (z=%.2f)", qty, self.cfg.target, px, trig.z)

    def _manage_exit(self, now: dt.datetime, close: float) -> None:
        pos = self.position
        f = self.engine.features
        t = self.bar_index
        if self.risk.kill_switch_active():
            self._close_position(now, close, "kill_switch")
            return
        reason = pos.evaluator.step(
            elapsed_s=t - pos.entry_second, close=close, atr=f.atr[t],
            slope_d_rev=f.slope_d_rev[t], slope_s_flat=f.slope_s_flat[t], z=f.z[t],
        )
        if reason:
            self._close_position(now, close, reason)

    def _close_position(self, now: dt.datetime, ref_px: float, reason: str) -> None:
        pos = self.position
        if pos is None:
            return
        if self.execute:
            fill = self.venue.flatten(self.cfg.target)
            px = fill.price if fill else ref_px
        else:
            px = ref_px
        pnl = (px - pos.entry_px) * pos.qty
        self.risk.record_exit(pnl, now.date())
        self._event("exit", qty=pos.qty, px=px, reason=reason, net_pnl=pnl,
                    hold_s=self.bar_index - pos.entry_second)
        log.info("EXIT %d @ %.2f (%s) pnl=%.2f", pos.qty, px, reason, pnl)
        self.position = None

    # ── stream plumbing ──

    async def run(self) -> None:
        cfg = self.cfg
        today = dt.datetime.now(ET).date()
        equity = self.venue.equity() if self.execute else float(
            cfg.risk["account_equity_fallback"])
        self.risk.start_day(today, equity)
        log.info("session start: mode=%s equity=%.0f feed=%s",
                 "exec" if self.execute else "scan", equity, cfg.feed)

        from alpaca.data.enums import DataFeed
        from alpaca.data.live import StockDataStream

        stream = StockDataStream(cfg.alpaca_key, cfg.alpaca_secret,
                                 feed=DataFeed(cfg.feed))

        async def on_trade(t):
            sym = t.symbol
            if sym in self.agg:
                self.agg[sym].add_trade(t.timestamp.timestamp(), float(t.price),
                                        float(t.size or 0))

        stream.subscribe_trades(on_trade, cfg.target, cfg.leader)
        clock_task = asyncio.create_task(self._second_clock())
        stream_task = asyncio.create_task(stream._run_forever())
        try:
            await clock_task
        finally:
            stream_task.cancel()
            await stream.close()
        log.info("session done")

    async def _second_clock(self) -> None:
        grace = self.cfg.live["bar_publish_grace_ms"] / 1000
        last = int(dt.datetime.now(ET).timestamp())
        while not self.done:
            now = dt.datetime.now(ET)
            sec = int(now.timestamp())
            if sec > last:
                # close the *previous* second after the grace period
                await asyncio.sleep(grace)
                for s in range(last, sec):
                    if dt.datetime.fromtimestamp(s, ET).time() >= self.cfg.session_start:
                        self.on_second(s)
                last = sec
                if self.done:
                    break
            await asyncio.sleep(0.05)


def run_live(cfg: Config, execute: bool) -> None:
    asyncio.run(LiveTrader(cfg, execute=execute).run())
