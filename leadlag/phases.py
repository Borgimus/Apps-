"""Phases 2–4: opening-session conditioning, impulse event study, order flow."""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np

from .config import Config
from .data import Grid
from .stats import xcorr_profile

log = logging.getLogger("leadlag.phases")
BPS = 1e4


# ── Phase 2: conditional lead metrics + heatmap tables ─────────────────────


def session_lead_metric(x_es, x_spy, mask, lag_steps) -> dict | None:
    """Compact per-slice lead summary: positive-lag mass minus negative-lag
    mass, and the peak lag (ms handled by caller)."""
    xc = xcorr_profile(x_es, x_spy, mask, lag_steps)
    pos = np.nansum([v for k, v in xc.items() if k > 0])
    neg = np.nansum([v for k, v in xc.items() if k < 0])
    finite = {k: v for k, v in xc.items() if np.isfinite(v) and k != 0}
    if not finite:
        return None
    return {"lead_mass": float(pos - neg), "peak_lag_steps": int(max(finite, key=finite.get)),
            "xcorr": xc}


def phase2_conditioning(cfg: Config, grids: list[Grid]) -> dict:
    """Lead metric by (a) minute-after-open bucket, (b) session vol tercile,
    (c) session SPY volume tercile, (d) SPY effective-spread-proxy tercile."""
    lag_steps = [max(1, ms // cfg.grid["base_dt_ms"]) for ms in cfg.grid["xcorr_lags_ms"]]
    step_per_min = 60_000 // cfg.grid["base_dt_ms"]

    by_minute: dict[str, list[float]] = {}
    session_rows = []
    for g in grids:
        s0 = int(np.argmax(g.study))
        for a, b in cfg.phase2["minute_buckets"]:
            m = np.zeros(len(g), dtype=bool)
            m[s0 + a * step_per_min : s0 + b * step_per_min] = True
            m &= g.study
            r = session_lead_metric(g.x_es, g.x_spy, m, lag_steps)
            if r:
                by_minute.setdefault(f"{a:02d}-{b:02d}min", []).append(r["lead_mass"])
        # session-level covariates
        r_all = session_lead_metric(g.x_es, g.x_spy, g.study, lag_steps)
        if r_all is None:
            continue
        rspy = np.diff(g.x_spy)
        rspy = rspy[g.study[1:] & np.isfinite(rspy)]
        vol = float(np.std(rspy) * BPS)
        volume = float(np.nansum(g.vol_spy[g.study]))
        # effective-spread proxy: mean |1-step return| on trade-print steps
        prints = g.real_spy[1:] & g.study[1:]
        eff = float(np.nanmean(np.abs(np.diff(g.x_spy))[prints]) * BPS) if prints.any() else math.nan
        session_rows.append({"day": g.day, "lead_mass": r_all["lead_mass"],
                             "peak_lag_ms": r_all["peak_lag_steps"] * cfg.grid["base_dt_ms"],
                             "vol_bps": vol, "volume": volume, "spread_proxy_bps": eff})

    def by_tercile(key: str) -> dict:
        vals = np.array([r[key] for r in session_rows])
        lm = np.array([r["lead_mass"] for r in session_rows])
        qs = np.nanquantile(vals, [1 / 3, 2 / 3])
        out = {}
        for name, sel in (("low", vals <= qs[0]),
                          ("mid", (vals > qs[0]) & (vals <= qs[1])),
                          ("high", vals > qs[1])):
            out[name] = {"n": int(sel.sum()), "mean_lead_mass": float(np.nanmean(lm[sel]))}
        return out

    return {
        "by_minute": {k: {"n": len(v), "mean_lead_mass": float(np.mean(v))}
                      for k, v in sorted(by_minute.items())},
        "by_volatility": by_tercile("vol_bps"),
        "by_volume": by_tercile("volume"),
        "by_spread": by_tercile("spread_proxy_bps"),
        "session_rows": session_rows,
    }


# ── Phase 3: impulse event study ───────────────────────────────────────────


@dataclass
class Impulse:
    day: object
    t: int                  # grid index of impulse end
    sign: int
    magnitude_bps: float
    spy_path_bps: np.ndarray  # SPY log-price path relative to impulse start


def find_impulses(cfg: Config, g: Grid, thr_bps: float) -> list[Impulse]:
    dt_ms = cfg.grid["base_dt_ms"]
    w = cfg.phase3["impulse_window_ms"] // dt_ms
    quiet = cfg.phase3["quiet_pre_ms"] // dt_ms
    horizon = cfg.phase3["response_horizon_ms"] // dt_ms
    gap = cfg.phase3["min_gap_ms"] // dt_ms
    thr = thr_bps / BPS

    x = g.x_es
    mv = x[w:] - x[:-w]                       # ES move over window, ends at t=w..
    out: list[Impulse] = []
    last_t = -gap
    for i in range(quiet + w, len(x) - horizon):
        m = mv[i - w]
        if not np.isfinite(m) or abs(m) < thr:
            continue
        if not g.study[i] or i - last_t < gap:
            continue
        pre = x[i - w] - x[i - w - quiet]
        if not np.isfinite(pre) or abs(pre) > 0.4 * thr:
            continue
        spy0 = g.x_spy[i - w]
        path = (g.x_spy[i - w : i + horizon] - spy0) * BPS
        if not np.isfinite(path).all():
            continue
        out.append(Impulse(day=g.day, t=i, sign=int(np.sign(m)),
                           magnitude_bps=float(m * BPS), spy_path_bps=path))
        last_t = i
    return out


def phase3_event_study(cfg: Config, grids: list[Grid]) -> dict:
    dt_ms = cfg.grid["base_dt_ms"]
    w = cfg.phase3["impulse_window_ms"] // dt_ms
    results = {}
    for thr in cfg.phase3["impulse_thresholds_bps"]:
        evs: list[Impulse] = []
        for g in grids:
            evs.extend(find_impulses(cfg, g, thr))
        if len(evs) < 10:
            results[thr] = {"n": len(evs)}
            continue
        # orient paths by impulse sign: response = sign * spy_path
        paths = np.array([e.sign * e.spy_path_bps for e in evs])
        mags = np.array([abs(e.magnitude_bps) for e in evs])
        irf = paths.mean(axis=0)
        base = irf[w]                          # SPY response at ES-impulse end
        final = irf[-1]
        half = 0.5 * final
        # delay: first step after impulse end where mean response ≥ half of final
        delay_idx = next((i for i in range(w, len(irf)) if irf[i] >= half), None)
        # per-event continuation: SPY keeps impulse sign from +1s to +10s
        one_s = w + 1000 // dt_ms
        cont = paths[:, -1] > paths[:, one_s]
        results[thr] = {
            "n": len(evs),
            "mean_es_move_bps": float(mags.mean()),
            "spy_response_at_impulse_end_bps": float(base),
            "spy_response_final_bps": float(final),
            "response_ratio": float(final / mags.mean()),
            "half_response_delay_ms": (None if delay_idx is None
                                       else int((delay_idx - w) * dt_ms)),
            "p_continuation_1s_to_10s": float(cont.mean()),
            "irf_bps": irf.tolist(),
            "irf_dt_ms": dt_ms,
            "irf_impulse_end_idx": w,
        }
    return results


# ── Phase 4: aggressor order-flow predictiveness ───────────────────────────


def phase4_order_flow(cfg: Config, grids: list[Grid]) -> dict:
    dt_ms = cfg.grid["base_dt_ms"]
    out = {}
    for win_ms in cfg.phase4["imbalance_windows_ms"]:
        wsteps = win_ms // dt_ms
        for h_ms in cfg.phase4["forward_horizons_ms"]:
            h = h_ms // dt_ms
            ics = []
            for g in grids:
                flow = np.nan_to_num(g.flow_es)
                c = np.cumsum(flow)
                imb = c[wsteps:] - c[:-wsteps]          # trailing signed volume
                fwd = g.x_spy[wsteps + h :] - g.x_spy[wsteps : -h]
                n = min(len(imb) - h, len(fwd))
                m = g.study[wsteps : wsteps + n] & np.isfinite(fwd[:n])
                if m.sum() < 200:
                    continue
                a, b = imb[:n][m], fwd[:n][m]
                if a.std() == 0 or b.std() == 0:
                    continue
                # rank IC (Spearman) is robust to flow fat tails
                ar = np.argsort(np.argsort(a)); br = np.argsort(np.argsort(b))
                ics.append(float(np.corrcoef(ar, br)[0, 1]))
            arr = np.array(ics)
            key = f"imb{win_ms}ms_fwd{h_ms}ms"
            out[key] = {
                "n_sessions": len(arr),
                "mean_rank_ic": float(np.nanmean(arr)) if len(arr) else math.nan,
                "t_stat": float(np.nanmean(arr) / (np.nanstd(arr) / math.sqrt(len(arr))))
                if len(arr) > 2 and np.nanstd(arr) > 0 else math.nan,
                "frac_positive": float(np.mean(arr > 0)) if len(arr) else math.nan,
            }
    return out
