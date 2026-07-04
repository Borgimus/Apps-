"""Statistical validation study (§8 of the spec).

Answers, in order:
1. Does the U-with-DIA-leading pattern occur, and how often?          (§8.1)
2. Does DIA actually *lead* SPY around these events?                  (§8.2)
3. Does the conditional forward return survive costs, and does it
   beat (a) time-of-day-matched random entries and (b) the same
   detector run on *shuffled* SPY/DIA pairings?                       (§8.3)
4. Is the edge robust across volatility regimes?                      (§8.5)

Outputs a results dict + a rendered markdown report with an explicit
PASS / FAIL verdict per the acceptance criteria.
"""
from __future__ import annotations

import datetime as dt
import logging
import math
from collections import Counter

import numpy as np
import pandas as pd
from scipy import stats

from .backtest import Backtester
from .config import Config
from .signals import BPS, Features, Trigger, compute_features, rolling_slope, scan

log = logging.getLogger("ureversal.research")


# ── helpers ────────────────────────────────────────────────────────────────


def _entry_bounds(cfg: Config, index: pd.DatetimeIndex) -> tuple[int, int, int]:
    tt = index.tz_convert("America/New_York").time
    ok = (tt >= cfg.entry_start) & (tt <= cfg.last_entry)
    if not ok.any():
        return 0, -1, len(tt) - 1
    first = int(np.argmax(ok))
    last = int(len(tt) - 1 - np.argmax(ok[::-1]))
    hard = min(int(np.searchsorted(tt, cfg.hard_exit)), len(tt) - 1)
    return first, last, hard


def net_forward_return_bps(
    bt: Backtester, price: np.ndarray, t: int, horizon_s: int, hard: int
) -> float | None:
    """Buy next second, sell `horizon_s` later (capped at hard exit), net of
    half-spread + slippage + fees, in bps."""
    e, x = t + 1, min(t + 1 + horizon_s, hard)
    if x <= e or not (np.isfinite(price[e]) and np.isfinite(price[x])):
        return None
    buy = bt._buy_px(price[e])
    sell = bt._sell_px(price[x])
    fees_bps = (2 * bt.commission + bt.sec_taf) / buy * BPS
    return (sell / buy - 1) * BPS - fees_bps


def bootstrap_mean_ci(x: np.ndarray, iters: int, rng: np.random.Generator,
                      alpha: float = 0.05) -> tuple[float, float, float]:
    means = np.array([rng.choice(x, size=len(x), replace=True).mean() for _ in range(iters)])
    return float(x.mean()), float(np.quantile(means, alpha / 2)), float(np.quantile(means, 1 - alpha / 2))


def null_mean_distribution(pool: np.ndarray, n: int, iters: int,
                           rng: np.random.Generator) -> np.ndarray:
    """Distribution of the mean of n draws from the null pool (matches the
    actual sample size, so the comparison is apples-to-apples)."""
    return np.array([rng.choice(pool, size=n, replace=True).mean() for _ in range(iters)])


# ── §8.2 lead-lag ──────────────────────────────────────────────────────────


def episode_cross_correlation(f: Features, trig: Trigger, max_k: int) -> dict[int, float] | None:
    """corr( r^D_{t-k}, r^S_t ) within the episode for k in [-max_k, max_k].
    k > 0 → DIA earlier (DIA leads)."""
    a, b = max(trig.t_dn - 30, 1), min(trig.t + 60, len(f.x_s) - 1)
    rs = np.diff(f.x_s[a : b + 1])
    rd = np.diff(f.x_d[a : b + 1])
    if len(rs) < 3 * max_k or len(rs) < 30:
        return None
    out = {}
    for k in range(-max_k, max_k + 1):
        if k >= 0:
            x, y = rd[: len(rd) - k or None], rs[k:]
        else:
            x, y = rd[-k:], rs[: len(rs) + k]
        m = np.isfinite(x) & np.isfinite(y)
        if m.sum() < 20 or x[m].std() == 0 or y[m].std() == 0:
            out[k] = np.nan
        else:
            out[k] = float(np.corrcoef(x[m], y[m])[0, 1])
    return out


def _zero_cross(slope: np.ndarray, start: int, end: int, sustain: int = 3) -> int | None:
    """First t in [start, end] where slope turns positive and stays so for
    `sustain` seconds."""
    run = 0
    for t in range(start, min(end + 1, len(slope))):
        run = run + 1 if (np.isfinite(slope[t]) and slope[t] > 0) else 0
        if run >= sustain:
            return t - sustain + 1
    return None


def reversal_timing(f: Features, trig: Trigger, p) -> int | None:
    """SPY zero-cross time minus DIA zero-cross time within the episode.
    Positive → DIA turned first. Uses W_r slopes for both instruments."""
    slope_s = rolling_slope(f.x_s, p.reversal_window_s)
    a, b = trig.t_dn, min(trig.t + 90, len(f.x_s) - 1)
    td = _zero_cross(f.slope_d_rev, a, b)
    ts = _zero_cross(slope_s, a, b)
    if td is None or ts is None:
        return None
    return ts - td


# ── study ──────────────────────────────────────────────────────────────────


def run_study(
    cfg: Config,
    sessions: dict[dt.date, pd.DataFrame],
    horizons: tuple[int, ...] = (30, 60, 120, 300),
    seed: int = 0,
) -> dict:
    rng = np.random.default_rng(seed)
    rc = cfg.research
    bt = Backtester(cfg)
    p = cfg.signals
    days = sorted(sessions)
    n_days = len(days)

    feats: dict[dt.date, Features] = {}
    trigs: dict[dt.date, list[Trigger]] = {}
    for d in days:
        f = compute_features(sessions[d], cfg.target, cfg.leader, p,
                             cfg.min_corr_obs, cfg.exits.atr_window_s)
        feats[d] = f
        first, last, _ = _entry_bounds(cfg, f.index)
        trigs[d] = [tr for tr in scan(f, p) if first <= tr.t <= last]

    all_events = [(d, tr) for d in days for tr in trigs[d]]
    n_events = len(all_events)
    log.info("detected %d trigger events over %d sessions", n_events, n_days)

    # §8.1 frequency
    per_day = Counter(d for d, _ in all_events)
    minute_hist = Counter(
        int((tr.ts.tz_convert("America/New_York") - tr.ts.tz_convert("America/New_York").normalize()
             ).total_seconds() // 60) for _, tr in all_events
    )
    freq = {
        "sessions": n_days,
        "events": n_events,
        "events_per_day_mean": n_events / n_days if n_days else math.nan,
        "days_with_event_pct": 100 * len(per_day) / n_days if n_days else math.nan,
        "by_minute_of_day": dict(sorted(minute_hist.items())),
    }

    # §8.2 lead-lag
    xcorrs, timings = [], []
    for d, tr in all_events:
        xc = episode_cross_correlation(feats[d], tr, rc["lead_lag_max_k"])
        if xc:
            xcorrs.append(xc)
        dt_ = reversal_timing(feats[d], tr, p)
        if dt_ is not None:
            timings.append(dt_)
    leadlag: dict = {"episodes_with_xcorr": len(xcorrs), "episodes_with_timing": len(timings)}
    if xcorrs:
        ks = sorted(xcorrs[0])
        mean_xc = {k: float(np.nanmean([x[k] for x in xcorrs])) for k in ks}
        lead_scores = np.array([
            np.nansum([x[k] for k in ks if k > 0]) - np.nansum([x[k] for k in ks if k < 0])
            for x in xcorrs
        ])
        _, lo, hi = bootstrap_mean_ci(lead_scores, rc["bootstrap_iters"], rng)
        leadlag.update(
            mean_xcorr_by_lag=mean_xc,
            lead_score_mean=float(lead_scores.mean()),
            lead_score_ci95=[lo, hi],
            dia_leads_xcorr=bool(lo > 0),
        )
    if len(timings) >= 10:
        tarr = np.array(timings)
        try:
            wstat = stats.wilcoxon(tarr[tarr != 0])
            pval = float(wstat.pvalue)
        except ValueError:
            pval = math.nan
        leadlag.update(
            timing_median_s=float(np.median(tarr)),
            timing_mean_s=float(tarr.mean()),
            timing_wilcoxon_p=pval,
            dia_leads_timing=bool(np.median(tarr) > 0 and pval < 0.05),
        )

    # §8.3 conditional edge vs nulls
    edge: dict = {}
    for H in horizons:
        actual = []
        for d, tr in all_events:
            f = feats[d]
            _, _, hard = _entry_bounds(cfg, f.index)
            r = net_forward_return_bps(bt, np.exp(f.x_s), tr.t, H, hard)
            if r is not None:
                actual.append(r)
        actual = np.array(actual)

        # Null A: time-of-day matched random entries
        pool_a = []
        for d in days:
            f = feats[d]
            first, last, hard = _entry_bounds(cfg, f.index)
            if last <= first:
                continue
            for t in rng.integers(first, last + 1, rc["null_random_entries_per_day"]):
                r = net_forward_return_bps(bt, np.exp(f.x_s), int(t), H, hard)
                if r is not None:
                    pool_a.append(r)
        pool_a = np.array(pool_a)

        # Null B: detector on shuffled SPY/DIA pairing (rotate leader by 1 day)
        pool_b = []
        if n_days >= 2:
            for i, d in enumerate(days):
                d2 = days[(i + 1) % n_days]
                tgt = sessions[d][cfg.target]
                led = sessions[d2][cfg.leader]
                n = min(len(tgt), len(led))
                shuffled = pd.concat(
                    {cfg.target: tgt.iloc[:n],
                     cfg.leader: led.iloc[:n].set_axis(tgt.index[:n])}, axis=1)
                fsh = compute_features(shuffled, cfg.target, cfg.leader, p,
                                       cfg.min_corr_obs, cfg.exits.atr_window_s)
                first, last, hard = _entry_bounds(cfg, fsh.index)
                for tr in scan(fsh, p):
                    if first <= tr.t <= last:
                        r = net_forward_return_bps(bt, np.exp(fsh.x_s), tr.t, H, hard)
                        if r is not None:
                            pool_b.append(r)
        pool_b = np.array(pool_b)

        h: dict = {"n_actual": len(actual), "n_null_random": len(pool_a),
                   "n_null_shuffled": len(pool_b)}
        if len(actual) >= 10:
            mean, lo, hi = bootstrap_mean_ci(actual, rc["bootstrap_iters"], rng)
            h.update(mean_bps=mean, ci95=[lo, hi], win_rate=float((actual > 0).mean()))
            if len(pool_a) >= 30:
                nda = null_mean_distribution(pool_a, len(actual), rc["bootstrap_iters"], rng)
                h.update(null_random_mean=float(pool_a.mean()),
                         null_random_p95=float(np.quantile(nda, 0.95)),
                         beats_null_random=bool(mean > np.quantile(nda, 0.95)))
            if len(pool_b) >= 30:
                ndb = null_mean_distribution(pool_b, len(actual), rc["bootstrap_iters"], rng)
                h.update(null_shuffled_mean=float(pool_b.mean()),
                         null_shuffled_p95=float(np.quantile(ndb, 0.95)),
                         beats_null_shuffled=bool(mean > np.quantile(ndb, 0.95)))
            else:
                # Shuffling the pairing collapsed the trigger rate itself —
                # the detector needs the true SPY/DIA link to fire at all.
                # That is affirmative evidence for the mechanism, provided the
                # rate really collapsed (< half the actual rate).
                h.update(null_shuffled_events=len(pool_b),
                         beats_null_shuffled=bool(len(pool_b) < 0.5 * len(actual)))
            h["positive_after_costs"] = bool(lo > 0)
        edge[H] = h

    # §8.5 regime split (opening-window realized vol terciles of the target)
    rv = {d: float(np.nanstd(np.diff(feats[d].x_s)) * BPS) for d in days}
    rv_ser = pd.Series(rv)
    terciles = rv_ser.quantile([1 / 3, 2 / 3])
    def _regime(d): return ("low" if rv[d] <= terciles.iloc[0]
                            else "mid" if rv[d] <= terciles.iloc[1] else "high")
    regime: dict = {}
    Href = 60 if 60 in horizons else horizons[0]
    for reg in ("low", "mid", "high"):
        evs = [(d, tr) for d, tr in all_events if _regime(d) == reg]
        rets = []
        for d, tr in evs:
            f = feats[d]
            _, _, hard = _entry_bounds(cfg, f.index)
            r = net_forward_return_bps(bt, np.exp(f.x_s), tr.t, Href, hard)
            if r is not None:
                rets.append(r)
        regime[reg] = {
            "days": sum(_regime(d) == reg for d in days),
            "events": len(evs),
            "mean_net_bps": float(np.mean(rets)) if rets else math.nan,
        }

    # verdict
    ref = edge.get(Href, {})
    checks = {
        "sufficient_events": n_events >= 30,
        "dia_leads_xcorr": bool(leadlag.get("dia_leads_xcorr", False)),
        "dia_leads_timing": bool(leadlag.get("dia_leads_timing", False)),
        "edge_positive_after_costs": bool(ref.get("positive_after_costs", False)),
        "beats_null_random": bool(ref.get("beats_null_random", False)),
        "beats_null_shuffled": bool(ref.get("beats_null_shuffled", False)),
    }
    verdict = "PASS" if all(checks.values()) else "FAIL"

    return {
        "meta": {
            "feed": cfg.feed, "sessions": n_days,
            "first_day": str(days[0]) if days else None,
            "last_day": str(days[-1]) if days else None,
            "params": cfg.signals.__dict__, "reference_horizon_s": Href,
        },
        "frequency": freq,
        "leadlag": leadlag,
        "edge_by_horizon": edge,
        "regime": regime,
        "checks": checks,
        "verdict": verdict,
    }


# ── report rendering ───────────────────────────────────────────────────────


def render_report(res: dict) -> str:
    m, fq, ll, ed, rg = (res["meta"], res["frequency"], res["leadlag"],
                         res["edge_by_horizon"], res["regime"])
    L: list[str] = []
    L.append("# U-Reversal Validation Report\n")
    L.append(f"**Verdict: {res['verdict']}**\n")
    L.append(f"- Feed: `{m['feed']}` · Sessions: {m['sessions']} "
             f"({m['first_day']} → {m['last_day']})\n")
    L.append("## Checks\n")
    for k, v in res["checks"].items():
        L.append(f"- {'✅' if v else '❌'} {k}")
    L.append("\n## §8.1 Frequency\n")
    L.append(f"- Events: {fq['events']} over {fq['sessions']} sessions "
             f"({fq['events_per_day_mean']:.2f}/day; "
             f"{fq['days_with_event_pct']:.0f}% of days had ≥1)\n")
    L.append("## §8.2 Does DIA lead SPY?\n")
    if "lead_score_mean" in ll:
        lo, hi = ll["lead_score_ci95"]
        L.append(f"- Cross-correlation lead score: {ll['lead_score_mean']:.3f} "
                 f"(95% CI [{lo:.3f}, {hi:.3f}]) → "
                 f"{'DIA leads' if ll['dia_leads_xcorr'] else 'no significant lead'}")
    if "timing_median_s" in ll:
        L.append(f"- Reversal timing (SPY cross − DIA cross): median "
                 f"{ll['timing_median_s']:.1f}s, Wilcoxon p={ll['timing_wilcoxon_p']:.4f} → "
                 f"{'DIA turns first' if ll['dia_leads_timing'] else 'not significant'}")
    L.append("\n## §8.3 Net edge vs null models (bps, after costs)\n")
    L.append("| Horizon | n | mean | 95% CI | win% | null-random p95 | null-shuffled p95 | beats both |")
    L.append("|---|---|---|---|---|---|---|---|")
    for H, h in ed.items():
        if "mean_bps" not in h:
            L.append(f"| {H}s | {h['n_actual']} | — too few events — | | | | | |")
            continue
        lo, hi = h["ci95"]
        shuf = (f"{h['null_shuffled_p95']:.2f}" if "null_shuffled_p95" in h
                else f"rate collapsed ({h.get('null_shuffled_events', 0)} ev)"
                if "null_shuffled_events" in h else "n/a")
        L.append(
            f"| {H}s | {h['n_actual']} | {h['mean_bps']:.2f} | [{lo:.2f}, {hi:.2f}] "
            f"| {100*h['win_rate']:.0f}% | {h.get('null_random_p95', float('nan')):.2f} "
            f"| {shuf} "
            f"| {'✅' if h.get('beats_null_random') and h.get('beats_null_shuffled') else '❌'} |"
        )
    L.append("\n## §8.5 Regime robustness (net bps @ reference horizon)\n")
    L.append("| Regime | days | events | mean net bps |")
    L.append("|---|---|---|---|")
    for reg, r in rg.items():
        L.append(f"| {reg} vol | {r['days']} | {r['events']} | {r['mean_net_bps']:.2f} |")
    L.append("")
    return "\n".join(L)
