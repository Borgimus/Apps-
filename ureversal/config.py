"""Configuration loader for the U-Reversal strategy.

All tunables live in ureversal.yaml; Alpaca credentials and the live-trading
gate come from the environment (never from yaml).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from typing import Any

import yaml

CONFIG_PATH = Path(__file__).parent / "ureversal.yaml"


def _parse_et(s: str) -> time:
    h, m, sec = (list(map(int, s.split(":"))) + [0])[:3]
    return time(h, m, sec)


@dataclass
class SignalParams:
    """Parameters of §2–§3 of the spec. Flat dataclass so the optimizer can
    clone-and-override individual fields cheaply."""

    slope_window_s: int = 20
    corr_window_s: int = 30
    flat_window_s: int = 10
    reversal_window_s: int = 10
    zscore_window_s: int = 120
    episode_high_lookback_s: int = 120
    min_downtrend_s: int = 30
    min_correlation: float = 0.70
    min_down_slope_bps: float = 0.30
    min_cum_decline_bps: float = 8.0
    flat_slope_bps: float = 0.15
    velocity_reduction_ratio: float = 0.5
    new_low_tolerance_bps: float = 1.0
    z_lookback_s: int = 15
    max_bottoming_s: int = 90
    min_dia_up_slope_bps: float = 0.30
    min_dia_retrace: float = 0.10
    max_spy_lead_slope_bps: float = 0.15
    min_divergence_z: float = 1.5
    exit_divergence_z: float = -1.0

    def override(self, **kw: Any) -> "SignalParams":
        d = {**self.__dict__, **kw}
        return SignalParams(**d)


@dataclass
class ExitParams:
    mode: str = "composite"
    fixed_target_pct: float = 0.20
    stop_loss_pct: float = 0.15
    trail_atr_mult: float = 2.0
    atr_window_s: int = 30
    trail_pct: float = 0.10
    stall_seconds: int = 10
    time_stop_s: int = 120

    def override(self, **kw: Any) -> "ExitParams":
        return ExitParams(**{**self.__dict__, **kw})


@dataclass
class Config:
    raw: dict
    target: str
    leader: str
    feed: str
    cache_dir: Path
    session_start: time
    session_end: time
    entry_start: time
    entry_end: time
    last_entry: time
    hard_exit: time
    max_ffill_s: int
    min_corr_obs: int
    signals: SignalParams
    exits: ExitParams
    execution: dict
    risk: dict
    live: dict
    research: dict
    optimize: dict
    dashboard: dict

    # -- credentials / gates (environment only) --
    alpaca_key: str | None = field(default=None)
    alpaca_secret: str | None = field(default=None)
    live_trading_enabled: bool = False


def load_config(path: Path | str = CONFIG_PATH, **signal_overrides: Any) -> Config:
    raw = yaml.safe_load(Path(path).read_text())
    sig = SignalParams(**raw["signals"]).override(**signal_overrides)
    ex = ExitParams(**raw["exits"])
    return Config(
        raw=raw,
        target=raw["instruments"]["target"],
        leader=raw["instruments"]["leader"],
        feed=os.getenv("UREVERSAL_FEED", raw["data"]["feed"]),
        cache_dir=Path(raw["data"]["cache_dir"]),
        session_start=_parse_et(raw["data"]["session_start_et"]),
        session_end=_parse_et(raw["data"]["session_end_et"]),
        entry_start=_parse_et(raw["window"]["entry_start_et"]),
        entry_end=_parse_et(raw["window"]["entry_end_et"]),
        last_entry=_parse_et(raw["window"]["last_entry_et"]),
        hard_exit=_parse_et(raw["window"]["hard_exit_et"]),
        max_ffill_s=raw["data"]["max_ffill_s"],
        min_corr_obs=raw["data"]["min_corr_obs"],
        signals=sig,
        exits=ex,
        execution=raw["execution"],
        risk=raw["risk"],
        live=raw["live"],
        research=raw["research"],
        optimize=raw["optimize"],
        dashboard=raw["dashboard"],
        alpaca_key=os.getenv("ALPACA_API_KEY"),
        alpaca_secret=os.getenv("ALPACA_SECRET_KEY"),
        live_trading_enabled=os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "true",
    )
