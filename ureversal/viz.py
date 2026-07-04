"""Visualization: session/event charts and equity curves (matplotlib, PNG)."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .backtest import Trade
from .config import Config
from .signals import BPS, Features, Trigger, compute_features

_C = {"spy": "#3f74d9", "dia": "#d9843f", "trig": "#c23b3b",
      "entry": "#1f9d55", "exit": "#c23b3b", "z": "#7a5cc2"}


def plot_session(
    cfg: Config, bars: pd.DataFrame, day: dt.date, out: Path,
    triggers: list[Trigger] | None = None, trades: list[Trade] | None = None,
    features: Features | None = None,
) -> Path:
    f = features or compute_features(bars, cfg.target, cfg.leader, cfg.signals,
                                     cfg.min_corr_obs, cfg.exits.atr_window_s)
    idx = f.index
    norm_s = (f.x_s - np.nanmean(f.x_s[:60])) * BPS
    norm_d = (f.x_d - np.nanmean(f.x_d[:60])) * BPS

    fig, axes = plt.subplots(3, 1, figsize=(13, 9), sharex=True,
                             gridspec_kw={"height_ratios": [3, 2, 2]})
    ax0, ax1, ax2 = axes
    ax0.plot(idx, norm_s, color=_C["spy"], lw=0.9, label=cfg.target)
    ax0.plot(idx, norm_d, color=_C["dia"], lw=0.9, label=cfg.leader)
    ax0.set_ylabel("bps from open ref")
    ax0.set_title(f"{cfg.target}/{cfg.leader} U-Reversal — {day}")

    for tr in triggers or []:
        ax0.axvline(tr.ts, color=_C["trig"], lw=0.8, ls="--", alpha=0.7)
    for t in trades or []:
        ax0.annotate("▲", (t.entry_ts, norm_s[t.entry_t]), color=_C["entry"],
                     fontsize=11, ha="center")
        ax0.annotate("▼", (t.exit_ts, norm_s[t.exit_t]), color=_C["exit"],
                     fontsize=11, ha="center")
    ax0.legend(loc="upper right", fontsize=8)

    ax1.plot(idx, f.slope_s_flat, color=_C["spy"], lw=0.8, label=f"{cfg.target} slope (W_f)")
    ax1.plot(idx, f.slope_d_rev, color=_C["dia"], lw=0.8, label=f"{cfg.leader} slope (W_r)")
    ax1.axhline(0, color="gray", lw=0.5)
    ax1b = ax1.twinx()
    ax1b.plot(idx, f.corr, color="#999", lw=0.6, alpha=0.7)
    ax1b.set_ylabel("ρ", color="#999")
    ax1b.set_ylim(-1, 1)
    ax1.set_ylabel("slope bps/s")
    ax1.legend(loc="upper right", fontsize=8)

    ax2.plot(idx, f.z, color=_C["z"], lw=0.8)
    ax2.axhline(cfg.signals.min_divergence_z, color=_C["trig"], lw=0.6, ls=":")
    ax2.set_ylabel("divergence z")

    for ax in axes:
        ax.grid(alpha=0.25)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=idx.tz))
    for label, x in (("entry window", cfg.entry_start), ("", cfg.entry_end)):
        for ax in axes:
            ax.axvline(pd.Timestamp(dt.datetime.combine(day, x, tzinfo=idx.tz)),
                       color="green", lw=0.6, alpha=0.4)
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=130)
    plt.close(fig)
    return out


def plot_equity(daily_pnl: pd.Series, equity0: float, out: Path,
                title: str = "Equity curve (net)") -> Path:
    curve = equity0 + daily_pnl.sort_index().cumsum()
    dd = curve - curve.cummax()
    fig, (ax0, ax1) = plt.subplots(2, 1, figsize=(11, 6), sharex=True,
                                   gridspec_kw={"height_ratios": [3, 1]})
    ax0.plot(curve.index, curve.values, color=_C["spy"], lw=1.2)
    ax0.set_ylabel("equity $")
    ax0.set_title(title)
    ax1.fill_between(dd.index, dd.values, 0, color=_C["exit"], alpha=0.5)
    ax1.set_ylabel("drawdown $")
    for ax in (ax0, ax1):
        ax.grid(alpha=0.25)
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=130)
    plt.close(fig)
    return out
