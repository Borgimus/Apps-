"""Event-driven backtester on 1-second bars.

Conservative fill model (§6 of the spec):
- Signal at close of second t*; entry eligible from t*+1.
- Marketable limit: no fill (trade skipped) if the next bar gapped more than
  `limit_offset_cap_usd` above the signal price.
- Buys pay close + half_spread + slippage; sells receive close − half_spread −
  slippage − SEC/TAF. Close-based exits only — no optimistic intrabar touches.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field
from typing import Iterable

import numpy as np
import pandas as pd

from .config import Config, ExitParams, SignalParams
from .signals import BPS, Features, Trigger, compute_features, scan


@dataclass
class Trade:
    day: dt.date
    entry_t: int
    entry_ts: pd.Timestamp
    entry_px: float
    exit_t: int
    exit_ts: pd.Timestamp
    exit_px: float
    qty: int
    gross_pnl: float
    fees: float
    net_pnl: float
    net_ret_bps: float
    hold_s: int
    exit_reason: str
    trigger: Trigger


@dataclass
class Metrics:
    n_trades: int = 0
    n_days: int = 0
    win_rate: float = math.nan
    profit_factor: float = math.nan
    expectancy_bps: float = math.nan
    expectancy_usd: float = math.nan
    sharpe_trade: float = math.nan
    sharpe_daily_ann: float = math.nan
    max_drawdown_usd: float = math.nan
    max_drawdown_pct: float = math.nan
    avg_hold_s: float = math.nan
    total_net_pnl: float = math.nan
    skipped_entries: int = 0

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


@dataclass
class BacktestResult:
    trades: list[Trade]
    daily_pnl: pd.Series
    metrics: Metrics
    params: SignalParams
    exits: ExitParams
    skipped_entries: int = 0


class ExitEvaluator:
    """Stateful §4 exit logic, stepped once per second. Shared verbatim by the
    backtester and the live trader so exit behavior cannot diverge."""

    def __init__(self, x: ExitParams, p: SignalParams, entry_px: float):
        self.x, self.p = x, p
        self.entry_px = entry_px
        self.target = entry_px * (1 + x.fixed_target_pct / 100)
        self.stop = entry_px * (1 - x.stop_loss_pct / 100)
        self.hi = entry_px
        self.stall = 0

    def step(self, elapsed_s: int, close: float, atr: float,
             slope_d_rev: float, slope_s_flat: float, z: float) -> str | None:
        x = self.x
        mode = x.mode
        self.hi = max(self.hi, close)
        if mode in ("fixed", "composite"):
            if close >= self.target:
                return "target"
            if close <= self.stop:
                return "stop"
        if mode in ("trailing_atr", "composite"):
            if np.isfinite(atr) and close < self.hi - x.trail_atr_mult * atr:
                return "trail_atr"
        if mode == "trailing_pct":
            if close < self.hi * (1 - x.trail_pct / 100):
                return "trail_pct"
        if mode in ("momentum", "composite"):
            if np.isfinite(slope_d_rev) and slope_d_rev < 0:
                return "dia_momentum_lost"
            self.stall = self.stall + 1 if (np.isfinite(slope_s_flat) and slope_s_flat < 0) else 0
            if self.stall >= x.stall_seconds:
                return "spy_stalled"
            if np.isfinite(z) and z < self.p.exit_divergence_z:
                return "divergence_gone"
        if mode in ("time", "composite") and elapsed_s >= x.time_stop_s:
            return "time_stop"
        return None


class Backtester:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        ex = cfg.execution
        self.half_spread = ex["half_spread_usd"]
        self.slippage_bps = ex["slippage_bps"]
        self.offset_cap = ex["limit_offset_cap_usd"]
        self.commission = ex["commission_per_share"]
        self.sec_taf = ex["sec_taf_per_share_sell"]
        self.equity = float(cfg.risk["account_equity_fallback"])
        self.alloc = cfg.risk["max_alloc_pct"] / 100.0
        self.max_trades_day = cfg.risk["max_trades_per_day"]

    # ── fills ──

    def _buy_px(self, close: float) -> float:
        return close + self.half_spread[self.cfg.target] + close * self.slippage_bps / BPS

    def _sell_px(self, close: float) -> float:
        return close - self.half_spread[self.cfg.target] - close * self.slippage_bps / BPS

    # ── exit engine (§4) ──

    def _find_exit(
        self, f: Features, entry_fill_t: int, entry_px: float,
        hard_exit_t: int, x: ExitParams, p: SignalParams,
    ) -> tuple[int, str]:
        """Return (exit bar index, reason) by stepping the shared ExitEvaluator
        over the seconds after the fill."""
        ev = ExitEvaluator(x, p, entry_px)
        price = np.exp(f.x_s)
        for t in range(entry_fill_t + 1, hard_exit_t):
            if not np.isfinite(price[t]):
                continue
            reason = ev.step(
                elapsed_s=t - entry_fill_t, close=price[t], atr=f.atr[t],
                slope_d_rev=f.slope_d_rev[t], slope_s_flat=f.slope_s_flat[t], z=f.z[t],
            )
            if reason:
                return t, reason
        return hard_exit_t, "hard_exit"

    # ── session / multi-session runs ──

    def _session_indices(self, index: pd.DatetimeIndex) -> tuple[int, int, int]:
        """(first allowed entry idx, last allowed entry idx, hard exit idx)."""
        tt = index.tz_convert("America/New_York").time
        entry_ok = (tt >= self.cfg.entry_start) & (tt <= self.cfg.last_entry)
        first = int(np.argmax(entry_ok)) if entry_ok.any() else len(tt)
        last = int(len(tt) - 1 - np.argmax(entry_ok[::-1])) if entry_ok.any() else -1
        hard = np.searchsorted(tt, self.cfg.hard_exit)
        return first, last, min(int(hard), len(tt) - 1)

    def run_session(
        self, bars: pd.DataFrame, day: dt.date,
        p: SignalParams, x: ExitParams,
        features: Features | None = None,
        triggers: list[Trigger] | None = None,
    ) -> tuple[list[Trade], int]:
        f = features if features is not None else compute_features(
            bars, self.cfg.target, self.cfg.leader, p, self.cfg.min_corr_obs, x.atr_window_s
        )
        if triggers is None:
            triggers = scan(f, p)
        first, last, hard = self._session_indices(f.index)
        price = np.exp(f.x_s)
        trades: list[Trade] = []
        skipped = 0
        busy_until = -1
        for trig in triggers:
            if not (first <= trig.t <= last) or trig.t < busy_until:
                continue
            if len(trades) >= self.max_trades_day:
                break
            ft = trig.t + 1  # fill bar
            if ft >= hard or not np.isfinite(price[ft]):
                continue
            if price[ft] > price[trig.t] + self.offset_cap:
                skipped += 1  # marketable limit not reached — no chase
                continue
            entry_px = self._buy_px(price[ft])
            qty = int(self.equity * self.alloc / entry_px)
            if qty <= 0:
                continue
            et, reason = self._find_exit(f, ft, entry_px, hard, x, p)
            exit_px = self._sell_px(price[et])
            gross = (exit_px - entry_px) * qty
            fees = qty * (2 * self.commission + self.sec_taf)
            net = gross - fees
            trades.append(
                Trade(
                    day=day, entry_t=ft, entry_ts=f.index[ft], entry_px=entry_px,
                    exit_t=et, exit_ts=f.index[et], exit_px=exit_px, qty=qty,
                    gross_pnl=gross, fees=fees, net_pnl=net,
                    net_ret_bps=(exit_px / entry_px - 1) * BPS,
                    hold_s=et - ft, exit_reason=reason, trigger=trig,
                )
            )
            busy_until = et + 1
        return trades, skipped

    def run(
        self,
        sessions: Iterable[tuple[dt.date, pd.DataFrame]],
        p: SignalParams | None = None,
        x: ExitParams | None = None,
    ) -> BacktestResult:
        p = p or self.cfg.signals
        x = x or self.cfg.exits
        all_trades: list[Trade] = []
        daily: dict[dt.date, float] = {}
        skipped = 0
        for day, bars in sessions:
            trades, sk = self.run_session(bars, day, p, x)
            skipped += sk
            all_trades.extend(trades)
            daily[day] = sum(t.net_pnl for t in trades)
        dser = pd.Series(daily).sort_index()
        return BacktestResult(
            trades=all_trades, daily_pnl=dser,
            metrics=compute_metrics(all_trades, dser, self.equity, skipped),
            params=p, exits=x, skipped_entries=skipped,
        )


def compute_metrics(
    trades: list[Trade], daily_pnl: pd.Series, equity: float, skipped: int = 0
) -> Metrics:
    m = Metrics(n_trades=len(trades), n_days=len(daily_pnl), skipped_entries=skipped)
    if not trades:
        return m
    pnl = np.array([t.net_pnl for t in trades])
    rets = np.array([t.net_ret_bps for t in trades])
    wins, losses = pnl[pnl > 0], pnl[pnl < 0]
    m.win_rate = float((pnl > 0).mean())
    m.profit_factor = float(wins.sum() / -losses.sum()) if losses.sum() < 0 else math.inf
    m.expectancy_bps = float(rets.mean())
    m.expectancy_usd = float(pnl.mean())
    m.sharpe_trade = float(rets.mean() / rets.std(ddof=1)) if len(rets) > 1 and rets.std(ddof=1) > 0 else math.nan
    active = daily_pnl[daily_pnl != 0]
    if len(active) > 1 and active.std(ddof=1) > 0:
        m.sharpe_daily_ann = float(active.mean() / active.std(ddof=1) * math.sqrt(252))
    curve = equity + daily_pnl.sort_index().cumsum()
    peak = curve.cummax()
    dd = curve - peak
    m.max_drawdown_usd = float(-dd.min())
    m.max_drawdown_pct = float((-dd / peak).max() * 100)
    m.avg_hold_s = float(np.mean([t.hold_s for t in trades]))
    m.total_net_pnl = float(pnl.sum())
    return m
