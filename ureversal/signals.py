"""Signal engine — implements §2–§3 of STRATEGY_SPEC.md exactly once.

Two consumption modes, one state machine:

- `compute_features()` + `scan()`  — vectorized precompute + sequential scan,
  used by research / backtest / optimizer (fast over many sessions).
- `SignalEngine`                   — incremental per-second updates, used by
  the live scanner and the replay system.

Both modes step the same `StateMachine` with identically defined features, and
the test suite asserts trigger-for-trigger parity between them on synthetic
sessions. Every quantity at second t uses data up to and including t.
"""
from __future__ import annotations

import enum
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import Config, SignalParams

BPS = 1e4


# ── rolling math (definitions of §2) ───────────────────────────────────────


def _slope_weights(w: int) -> np.ndarray:
    i = np.arange(w, dtype=float)
    i -= i.mean()
    return i / (i @ i)


_WEIGHTS: dict[int, np.ndarray] = {}


def slope_weights(w: int) -> np.ndarray:
    if w not in _WEIGHTS:
        _WEIGHTS[w] = _slope_weights(w)
    return _WEIGHTS[w]


def rolling_slope(x: np.ndarray, w: int) -> np.ndarray:
    """OLS slope of x on time over trailing window w, in bps per second.
    First w-1 entries are NaN."""
    out = np.full(len(x), np.nan)
    if len(x) >= w:
        out[w - 1 :] = np.convolve(x, slope_weights(w)[::-1], mode="valid") * BPS
    return out


def rolling_corr_masked(
    rs: np.ndarray, rd: np.ndarray, valid: np.ndarray, w: int, min_obs: int
) -> np.ndarray:
    """Pearson corr of the two return series over trailing w seconds, using only
    seconds where both instruments actually printed (valid mask). NaN when
    fewer than min_obs valid pairs."""
    n = len(rs)
    v = valid.astype(float)
    a = np.where(valid, rs, 0.0)
    b = np.where(valid, rd, 0.0)

    def rsum(z: np.ndarray) -> np.ndarray:
        c = np.cumsum(z)
        out = c.copy()
        out[w:] = c[w:] - c[:-w]
        return out

    cnt = rsum(v)
    sa, sb = rsum(a), rsum(b)
    saa, sbb, sab = rsum(a * a), rsum(b * b), rsum(a * b)
    with np.errstate(invalid="ignore", divide="ignore"):
        cov = sab - sa * sb / cnt
        va = saa - sa * sa / cnt
        vb = sbb - sb * sb / cnt
        corr = cov / np.sqrt(va * vb)
    corr[(cnt < min_obs) | ~np.isfinite(corr)] = np.nan
    corr[: w - 1] = np.nan
    return corr


def rolling_mean_std(x: np.ndarray, w: int, min_obs: int) -> tuple[np.ndarray, np.ndarray]:
    """NaN-aware rolling mean/std (ddof=1) over trailing w entries."""
    s = pd.Series(x)
    m = s.rolling(w, min_periods=min_obs).mean().to_numpy()
    sd = s.rolling(w, min_periods=min_obs).std().to_numpy()
    return m, sd


def rolling_extreme(x: np.ndarray, w: int, kind: str) -> np.ndarray:
    s = pd.Series(x)
    r = s.rolling(w, min_periods=1)
    return (r.max() if kind == "max" else r.min()).to_numpy()


# ── features ───────────────────────────────────────────────────────────────


@dataclass
class Features:
    """Per-second feature arrays for one session (aligned to the bar index)."""

    index: pd.DatetimeIndex
    x_s: np.ndarray           # log price target (SPY)
    x_d: np.ndarray           # log price leader (DIA)
    real_s: np.ndarray
    real_d: np.ndarray
    slope_s: np.ndarray       # β^S(W_s)
    slope_d: np.ndarray       # β^D(W_s)
    slope_s_flat: np.ndarray  # β^S(W_f)
    slope_s_rev: np.ndarray   # β^S(W_r)
    slope_d_rev: np.ndarray   # β^D(W_r)
    corr: np.ndarray          # ρ(W_ρ)
    z: np.ndarray             # divergence z-score
    z_recent_max: np.ndarray  # max z over trailing W_zl (trigger gate)
    hi_s: np.ndarray          # rolling max of x_s over W_hi
    hi_d: np.ndarray
    atr: np.ndarray           # rolling mean abs 1s move (for ATR trailing exits)


def compute_features(bars: pd.DataFrame, target: str, leader: str,
                     p: SignalParams, min_corr_obs: int,
                     atr_window_s: int = 30) -> Features:
    x_s = np.log(bars[(target, "close")].to_numpy(dtype=float))
    x_d = np.log(bars[(leader, "close")].to_numpy(dtype=float))
    real_s = bars[(target, "real")].to_numpy(dtype=bool)
    real_d = bars[(leader, "real")].to_numpy(dtype=bool)

    # Correlation uses overlapping k-second returns (k = corr_ret_s) — 1-second
    # returns are Epps-degraded (median SPY/DIA 1s corr ≈ 0.46 on real SIP data
    # vs ≈ 0.95 at minutes); k-second overlapping returns restore the coupling
    # the downtrend gate is actually about.
    k = max(p.corr_ret_s, 1)
    n = len(x_s)

    def lag(a: np.ndarray, fill) -> np.ndarray:
        out = np.full(n, fill, dtype=a.dtype if a.dtype != bool else bool)
        if n > k:
            out[k:] = a[: n - k]
        return out

    rs = x_s - lag(x_s, np.nan)
    rd = x_d - lag(x_d, np.nan)
    both_real = real_s & real_d
    valid = both_real & lag(both_real, False)
    valid &= np.isfinite(rs) & np.isfinite(rd)

    slope_s_rev = rolling_slope(x_s, p.reversal_window_s)
    slope_d_rev = rolling_slope(x_d, p.reversal_window_s)
    d = slope_d_rev - slope_s_rev
    m, sd = rolling_mean_std(d, p.zscore_window_s, min_obs=max(30, p.zscore_window_s // 4))
    with np.errstate(invalid="ignore", divide="ignore"):
        z = (d - m) / sd
    z[~np.isfinite(z)] = np.nan
    z_recent_max = pd.Series(z).rolling(p.z_lookback_s, min_periods=1).max().to_numpy()

    price_s = np.exp(x_s)
    abs_move = np.abs(np.diff(price_s, prepend=price_s[0]))
    atr = pd.Series(abs_move).rolling(atr_window_s, min_periods=5).mean().to_numpy()

    return Features(
        index=bars.index,
        x_s=x_s, x_d=x_d, real_s=real_s, real_d=real_d,
        slope_s=rolling_slope(x_s, p.slope_window_s),
        slope_d=rolling_slope(x_d, p.slope_window_s),
        slope_s_flat=rolling_slope(x_s, p.flat_window_s),
        slope_s_rev=slope_s_rev,
        slope_d_rev=slope_d_rev,
        corr=rolling_corr_masked(rs, rd, valid, p.corr_window_s, min_corr_obs),
        z=z,
        z_recent_max=z_recent_max,
        hi_s=rolling_extreme(x_s, p.episode_high_lookback_s, "max"),
        hi_d=rolling_extreme(x_d, p.episode_high_lookback_s, "max"),
        atr=atr,
    )


# ── state machine (§3) ─────────────────────────────────────────────────────


class State(enum.Enum):
    IDLE = "idle"
    DOWNTREND = "downtrend"
    BOTTOMING = "bottoming"


@dataclass
class Trigger:
    """A §3.3 entry trigger with episode diagnostics."""

    t: int                    # bar index within session
    ts: pd.Timestamp
    t_dn: int                 # downtrend confirmation index
    t_bottom: int             # bottoming confirmation index
    episode_hi_s: float       # log prices
    episode_lo_s: float
    episode_hi_d: float
    episode_lo_d: float
    spy_flat_slope: float
    dia_rev_slope: float
    corr_at_dn: float
    z: float


@dataclass
class StateMachine:
    """Sequential detector; step once per second with that second's features."""

    p: SignalParams
    state: State = State.IDLE
    down_hist: list = field(default_factory=list)  # decline_ok, last min_downtrend_s
    t_dn: int = -1
    t_bottom: int = -1
    last_decline_ok: int = -1
    hi_s: float = math.nan
    hi_d: float = math.nan
    lo_s: float = math.inf
    lo_d: float = math.inf
    t_last_low_s: int = -1
    dn_slope_sum: float = 0.0
    dn_slope_n: int = 0
    corr_at_dn: float = math.nan
    _flat_hist: dict[int, float] = field(default_factory=dict)

    def _reset(self) -> None:
        self.state = State.IDLE
        self.down_hist = []
        self.t_dn = self.t_bottom = self.last_decline_ok = -1
        self.hi_s = self.hi_d = math.nan
        self.lo_s = self.lo_d = math.inf
        self.t_last_low_s = -1
        self.dn_slope_sum = 0.0
        self.dn_slope_n = 0
        self.corr_at_dn = math.nan

    def step(self, t: int, f: Features) -> Trigger | None:
        p = self.p
        eps = p.new_low_tolerance_bps / BPS
        slope_s, slope_d = f.slope_s[t], f.slope_d[t]
        flat = f.slope_s_flat[t]
        self._flat_hist[t] = flat
        self._flat_hist.pop(t - p.flat_window_s - 2, None)

        # §3.1 downtrend conditions this second
        decline_ok = (
            np.isfinite(slope_s) and np.isfinite(slope_d) and np.isfinite(f.corr[t])
            and slope_s < -p.min_down_slope_bps
            and slope_d < -p.min_down_slope_bps
            and f.corr[t] >= p.min_correlation
            and (f.hi_s[t] - f.x_s[t]) * BPS >= p.min_cum_decline_bps
            and (f.hi_d[t] - f.x_d[t]) * BPS >= p.min_cum_decline_bps
        )
        self.down_hist.append(bool(decline_ok))
        if len(self.down_hist) > p.min_downtrend_s:
            self.down_hist.pop(0)
        if decline_ok:
            self.last_decline_ok = t

        if self.state == State.IDLE:
            # §3.1: conditions held in ≥ fill_frac of the last D_min seconds
            # (strict consecutiveness is unattainable on noisy 1s data — a
            # single second of correlation dip would reset the clock)
            if (
                decline_ok
                and len(self.down_hist) >= p.min_downtrend_s
                and sum(self.down_hist) >= p.downtrend_fill_frac * p.min_downtrend_s
            ):
                self.state = State.DOWNTREND
                self.t_dn = t
                self.hi_s, self.hi_d = f.hi_s[t], f.hi_d[t]
                w0 = max(0, t - p.min_downtrend_s + 1)
                self.lo_s = float(np.nanmin(f.x_s[w0 : t + 1]))
                self.lo_d = float(np.nanmin(f.x_d[w0 : t + 1]))
                self.t_last_low_s = t
                self.dn_slope_sum, self.dn_slope_n = 0.0, 0
                self.corr_at_dn = float(f.corr[t])
            return None

        # episode low maintenance (both non-idle states)
        if f.x_s[t] < self.lo_s - eps:
            self.lo_s = f.x_s[t]
            self.t_last_low_s = t
        else:
            self.lo_s = min(self.lo_s, f.x_s[t])
        self.lo_d = min(self.lo_d, f.x_d[t])

        if self.state == State.DOWNTREND:
            self.dn_slope_sum += slope_s if np.isfinite(slope_s) else 0.0
            self.dn_slope_n += np.isfinite(slope_s)

            # lapse: SPY already reversed without a detectable bottom, or stale
            if flat > p.max_spy_lead_slope_bps or (
                t - self.last_decline_ok > p.max_bottoming_s
            ):
                self._reset()
                return None

            # §3.2 flattening → BOTTOMING (evaluated concurrently with trigger:
            # a fast V-turn may confirm flattening and fire in the same second)
            mean_dn = self.dn_slope_sum / max(self.dn_slope_n, 1)
            flat_lag = self._flat_hist.get(t - p.flat_window_s, math.nan)
            if (
                np.isfinite(flat)
                and flat > -p.flat_slope_bps
                and abs(flat) <= p.velocity_reduction_ratio * abs(mean_dn)
                and np.isfinite(flat_lag)
                and flat - flat_lag > 0
            ):
                self.state = State.BOTTOMING
                self.t_bottom = t
            else:
                return None

        # state == BOTTOMING (possibly entered this same second)
        if f.x_s[t] < self.lo_s - eps or t - self.t_bottom > p.max_bottoming_s:
            self._reset()
            return None

        # §3.3 DIA leading reversal → TRIGGER
        rng_d = self.hi_d - self.lo_d
        retrace = (f.x_d[t] - self.lo_d) / rng_d if rng_d > 0 else 0.0
        if (
            np.isfinite(f.slope_d_rev[t])
            and f.slope_d_rev[t] > p.min_dia_up_slope_bps
            and retrace >= p.min_dia_retrace
            and np.isfinite(flat)
            and -p.flat_slope_bps <= flat <= p.max_spy_lead_slope_bps
            and np.isfinite(f.z_recent_max[t])
            and f.z_recent_max[t] >= p.min_divergence_z
        ):
            trig = Trigger(
                t=t, ts=f.index[t], t_dn=self.t_dn, t_bottom=self.t_bottom,
                episode_hi_s=self.hi_s, episode_lo_s=self.lo_s,
                episode_hi_d=self.hi_d, episode_lo_d=self.lo_d,
                spy_flat_slope=float(flat), dia_rev_slope=float(f.slope_d_rev[t]),
                corr_at_dn=self.corr_at_dn, z=float(f.z_recent_max[t]),
            )
            self._reset()
            return trig
        return None


def scan(features: Features, p: SignalParams,
         states_out: list | None = None) -> list[Trigger]:
    """Run the state machine across a whole session's features."""
    sm = StateMachine(p)
    triggers: list[Trigger] = []
    for t in range(len(features.x_s)):
        if not (np.isfinite(features.x_s[t]) and np.isfinite(features.x_d[t])):
            if states_out is not None:
                states_out.append(sm.state)
            continue
        trig = sm.step(t, features)
        if states_out is not None:
            states_out.append(sm.state)
        if trig:
            triggers.append(trig)
    return triggers


# ── incremental engine (live / replay) ─────────────────────────────────────


class SignalEngine:
    """Incremental wrapper: feed one (target, leader) bar pair per second.

    Internally grows the same arrays `compute_features` produces and steps the
    same StateMachine, so live behavior matches backtest by construction.
    """

    def __init__(self, cfg: Config, p: SignalParams | None = None):
        self.cfg = cfg
        self.p = p or cfg.signals
        self._rows: list[tuple[pd.Timestamp, float, bool, float, bool]] = []
        self._features: Features | None = None
        self._sm = StateMachine(self.p)

    @property
    def state(self) -> State:
        return self._sm.state

    def update(self, ts: pd.Timestamp, target_close: float, target_real: bool,
               leader_close: float, leader_real: bool) -> Trigger | None:
        self._rows.append((ts, target_close, target_real, leader_close, leader_real))
        t = len(self._rows) - 1
        # Recompute features over the session so far. Sessions are ≤ ~1700
        # seconds, so full recompute stays well under the 1-second budget;
        # correctness parity with the batch path is worth more than the CPU.
        idx = pd.DatetimeIndex([r[0] for r in self._rows])
        bars = pd.concat(
            {
                self.cfg.target: pd.DataFrame(
                    {"close": [r[1] for r in self._rows],
                     "real": [r[2] for r in self._rows]}, index=idx),
                self.cfg.leader: pd.DataFrame(
                    {"close": [r[3] for r in self._rows],
                     "real": [r[4] for r in self._rows]}, index=idx),
            },
            axis=1,
        )
        self._features = compute_features(
            bars, self.cfg.target, self.cfg.leader, self.p, self.cfg.min_corr_obs,
            atr_window_s=self.cfg.exits.atr_window_s,
        )
        if not (np.isfinite(self._features.x_s[t]) and np.isfinite(self._features.x_d[t])):
            return None
        return self._sm.step(t, self._features)

    @property
    def features(self) -> Features | None:
        return self._features
