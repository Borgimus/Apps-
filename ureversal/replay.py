"""Signal replay — drive a historical session through the LIVE code path.

Feeds recorded 1-second bars one at a time into `SignalEngine` (exactly what
the websocket loop does), simulates fills with the backtest cost model, and
cross-checks the incremental triggers against the vectorized `scan()` — any
mismatch is a parity bug and is reported loudly.
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .backtest import Backtester, ExitEvaluator, Trade
from .config import Config
from .data import DataStore
from .signals import BPS, SignalEngine, Trigger, compute_features, scan

log = logging.getLogger("ureversal.replay")


@dataclass
class ReplayResult:
    day: dt.date
    triggers: list[Trigger]
    trades: list[Trade]
    parity_ok: bool
    batch_trigger_ts: list[pd.Timestamp] = field(default_factory=list)
    incremental_trigger_ts: list[pd.Timestamp] = field(default_factory=list)
    bars: pd.DataFrame | None = None


def replay_session(cfg: Config, bars: pd.DataFrame, day: dt.date) -> ReplayResult:
    engine = SignalEngine(cfg)
    bt = Backtester(cfg)
    idx = bars.index
    tclose = bars[(cfg.target, "close")].to_numpy(dtype=float)
    treal = bars[(cfg.target, "real")].to_numpy(dtype=bool)
    lclose = bars[(cfg.leader, "close")].to_numpy(dtype=float)
    lreal = bars[(cfg.leader, "real")].to_numpy(dtype=bool)

    first, last, hard = bt._session_indices(idx)
    inc_triggers: list[Trigger] = []
    trades: list[Trade] = []
    pos: tuple[int, float, int, ExitEvaluator] | None = None  # qty, px, entry_t, ev

    for t in range(len(idx)):
        if not (np.isfinite(tclose[t]) and np.isfinite(lclose[t])):
            continue
        trig = engine.update(idx[t], tclose[t], treal[t], lclose[t], lreal[t])
        f = engine.features

        if pos:
            qty, epx, et0, ev = pos
            reason = None
            if t >= hard:
                reason = "hard_exit"
            else:
                reason = ev.step(elapsed_s=t - et0, close=tclose[t], atr=f.atr[t],
                                 slope_d_rev=f.slope_d_rev[t],
                                 slope_s_flat=f.slope_s_flat[t], z=f.z[t])
            if reason:
                xpx = bt._sell_px(tclose[t])
                fees = qty * (2 * bt.commission + bt.sec_taf)
                gross = (xpx - epx) * qty
                trades.append(Trade(
                    day=day, entry_t=et0, entry_ts=idx[et0], entry_px=epx,
                    exit_t=t, exit_ts=idx[t], exit_px=xpx, qty=qty,
                    gross_pnl=gross, fees=fees, net_pnl=gross - fees,
                    net_ret_bps=(xpx / epx - 1) * BPS, hold_s=t - et0,
                    exit_reason=reason, trigger=inc_triggers[-1]))
                pos = None

        if trig is not None:
            inc_triggers.append(trig)
            if pos is None and first <= t <= last and len(trades) < bt.max_trades_day:
                ft = t + 1
                if ft < hard and np.isfinite(tclose[ft- 1]):
                    # entry fills next second in live; approximate here at
                    # current close + costs (replay has no next bar yet at t)
                    epx = bt._buy_px(tclose[t])
                    qty = int(bt.equity * bt.alloc / epx)
                    if qty > 0:
                        pos = (qty, epx, t, ExitEvaluator(cfg.exits, cfg.signals, epx))

    # parity check vs the batch path
    f_batch = compute_features(bars, cfg.target, cfg.leader, cfg.signals,
                               cfg.min_corr_obs, cfg.exits.atr_window_s)
    batch = scan(f_batch, cfg.signals)
    b_ts = [tr.ts for tr in batch]
    i_ts = [tr.ts for tr in inc_triggers]
    parity = b_ts == i_ts
    if not parity:
        log.error("PARITY MISMATCH %s: batch=%s incremental=%s", day, b_ts, i_ts)
    return ReplayResult(day=day, triggers=inc_triggers, trades=trades,
                        parity_ok=parity, batch_trigger_ts=b_ts,
                        incremental_trigger_ts=i_ts, bars=bars)


def replay_date(cfg: Config, day: dt.date) -> ReplayResult | None:
    bars = DataStore(cfg).get_session(day)
    if bars is None:
        log.warning("no data for %s", day)
        return None
    return replay_session(cfg, bars, day)
