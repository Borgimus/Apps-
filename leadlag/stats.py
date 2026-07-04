"""Phase 1 — price discovery statistics, computed per session and aggregated.

All estimators take log-price arrays sampled on a regular grid and a study
mask (09:30–10:00 ET). k > 0 always means "ES leads SPY by k steps".
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np

log = logging.getLogger("leadlag.stats")


def _rets(x: np.ndarray, mask: np.ndarray) -> np.ndarray:
    r = np.diff(x)
    r[~(mask[1:] & mask[:-1])] = np.nan
    return r


# ── cross-correlation ──────────────────────────────────────────────────────


def xcorr_profile(x_es: np.ndarray, x_spy: np.ndarray, mask: np.ndarray,
                  lag_steps: list[int]) -> dict[int, float]:
    """corr( r_ES[t−k], r_SPY[t] ) for k ∈ ±lag_steps ∪ {0}."""
    ra, rb = _rets(x_es, mask), _rets(x_spy, mask)
    out = {}
    for k in sorted({0, *lag_steps, *[-abs(k) for k in lag_steps]}):
        if k >= 0:
            a, b = ra[: len(ra) - k or None], rb[k:]
        else:
            a, b = ra[-k:], rb[: len(rb) + k]
        m = np.isfinite(a) & np.isfinite(b)
        if m.sum() < 100 or a[m].std() == 0 or b[m].std() == 0:
            out[k] = np.nan
        else:
            out[k] = float(np.corrcoef(a[m], b[m])[0, 1])
    return out


# ── VAR / Granger ──────────────────────────────────────────────────────────


def granger_bidirectional(x_es: np.ndarray, x_spy: np.ndarray,
                          mask: np.ndarray, p: int) -> dict | None:
    """VAR(p) on the return pair; Wald tests for ES→SPY and SPY→ES."""
    from statsmodels.tsa.api import VAR

    ra, rb = _rets(x_es, mask), _rets(x_spy, mask)
    m = np.isfinite(ra) & np.isfinite(rb)
    if m.sum() < 50 * p:
        return None
    data = np.column_stack([ra[m], rb[m]]) * 1e4
    try:
        res = VAR(data).fit(p)
        es_to_spy = res.test_causality(caused=1, causing=0, kind="wald")
        spy_to_es = res.test_causality(caused=0, causing=1, kind="wald")
    except Exception:
        return None
    # summed cross-lag coefficients: SPY_t on ES lags (column 0 of eq 1)
    b_es_to_spy = float(sum(res.coefs[i][1, 0] for i in range(p)))
    b_spy_to_es = float(sum(res.coefs[i][0, 1] for i in range(p)))
    return {
        "p_es_to_spy": float(es_to_spy.pvalue),
        "p_spy_to_es": float(spy_to_es.pvalue),
        "coef_es_to_spy": b_es_to_spy,
        "coef_spy_to_es": b_spy_to_es,
        "nobs": int(m.sum()),
    }


# ── information shares (VECM on the log basis) ─────────────────────────────


def info_shares(x_es: np.ndarray, x_spy: np.ndarray, mask: np.ndarray,
                p: int) -> dict | None:
    """Bivariate ECM with cointegrating vector (1, −1) + constant (the log
    basis is stationary intraday). Returns Gonzalo-Granger component share
    and Hasbrouck information-share bounds for ES.

    ES is the price leader iff CS_ES ≈ 1 and the Hasbrouck bounds are high:
    that means SPY does the error-correcting while ES ignores the basis.
    """
    v = mask & np.isfinite(x_es) & np.isfinite(x_spy)
    xe, xs = x_es.copy(), x_spy.copy()
    z = xe - xs                       # log basis
    dz_es, dz_spy = np.diff(xe), np.diff(xs)
    ok = v[1:] & v[:-1] & np.isfinite(dz_es) & np.isfinite(dz_spy)
    if ok.sum() < 100 * (p + 1):
        return None
    # vectorized lagged design on points whose full lag window is valid
    okc = np.ones(len(ok), dtype=bool)
    for j in range(p + 1):
        okc[p:] &= ok[p - j : len(ok) - j]
    okc[:p] = False
    t = np.where(okc)[0]
    cols = [np.ones(len(t)), z[t]]
    for j in range(1, p + 1):
        cols.append(dz_es[t - j])
        cols.append(dz_spy[t - j])
    X = np.column_stack(cols)
    ye, ys = dz_es[t], dz_spy[t]
    if len(X) < 100:
        return None
    coef_e, *_ = np.linalg.lstsq(X, ye, rcond=None)
    coef_s, *_ = np.linalg.lstsq(X, ys, rcond=None)
    a_es, a_spy = coef_e[1], coef_s[1]
    res_e = ye - X @ coef_e
    res_s = ys - X @ coef_s
    omega = np.cov(np.column_stack([res_e, res_s]).T)

    denom = a_spy - a_es
    if abs(denom) < 1e-12:
        return None
    # common-factor weights (Gonzalo-Granger): γ ∝ α_perp
    g_es, g_spy = a_spy / denom, -a_es / denom
    cs_es = abs(g_es) / (abs(g_es) + abs(g_spy))

    def hasbrouck(order_first_es: bool) -> float:
        if order_first_es:
            F = np.linalg.cholesky(omega)
            g = np.array([g_es, g_spy])
        else:
            P = omega[::-1, ::-1]
            Fr = np.linalg.cholesky(P)
            F = Fr[::-1, ::-1]
            g = np.array([g_es, g_spy])
        contrib = (g @ F) ** 2
        tot = contrib.sum()
        return float(contrib[0] / tot) if tot > 0 else np.nan

    is_upper = hasbrouck(True)    # ES ordered first → upper bound for ES
    is_lower = hasbrouck(False)
    return {
        "alpha_es": float(a_es), "alpha_spy": float(a_spy),
        "gg_cs_es": float(cs_es),
        "hasbrouck_is_es_lower": min(is_lower, is_upper),
        "hasbrouck_is_es_upper": max(is_lower, is_upper),
        "nobs": len(X),
    }


# ── transfer entropy ───────────────────────────────────────────────────────


def _discretize(r: np.ndarray, bins: int) -> np.ndarray:
    q = np.nanquantile(r, np.linspace(0, 1, bins + 1)[1:-1])
    return np.digitize(r, q)


def _te(src: np.ndarray, dst: np.ndarray, bins: int) -> float:
    """TE(src→dst) with 1-step history, plug-in estimator (nats)."""
    y2, y1, x1 = dst[1:], dst[:-1], src[:-1]
    n = len(y2)
    joint = np.zeros((bins, bins, bins))
    np.add.at(joint, (y2, y1, x1), 1)
    joint /= n
    p_y1x1 = joint.sum(axis=0)
    p_y2y1 = joint.sum(axis=2)
    p_y1 = joint.sum(axis=(0, 2))
    te = 0.0
    for i in range(bins):
        for j in range(bins):
            for k in range(bins):
                pj = joint[i, j, k]
                if pj > 0 and p_y1x1[j, k] > 0 and p_y2y1[i, j] > 0 and p_y1[j] > 0:
                    te += pj * math.log(pj * p_y1[j] / (p_y1x1[j, k] * p_y2y1[i, j]))
    return te


def transfer_entropy(x_es: np.ndarray, x_spy: np.ndarray, mask: np.ndarray,
                     bins: int, n_perm: int = 50, seed: int = 0) -> dict | None:
    ra, rb = _rets(x_es, mask), _rets(x_spy, mask)
    m = np.isfinite(ra) & np.isfinite(rb)
    if m.sum() < 500:
        return None
    a = _discretize(ra[m], bins)
    b = _discretize(rb[m], bins)
    te_es_spy = _te(a, b, bins)
    te_spy_es = _te(b, a, bins)
    rng = np.random.default_rng(seed)
    null = np.array([_te(np.roll(a, rng.integers(100, len(a) - 100)), b, bins)
                     for _ in range(n_perm)])
    return {
        "te_es_to_spy": te_es_spy, "te_spy_to_es": te_spy_es,
        "te_net": te_es_spy - te_spy_es,
        "p_value": float((null >= te_es_spy).mean()),
    }


# ── per-session bundle + aggregation ───────────────────────────────────────


@dataclass
class SessionStats:
    day: object
    xcorr: dict[int, float]
    granger: dict | None
    ishares: dict | None
    te: dict | None


def aggregate(sessions: list[SessionStats], lag_steps: list[int],
              dt_ms: int) -> dict:
    ks = sorted({0, *lag_steps, *[-k for k in lag_steps]})
    xc = {k: np.array([s.xcorr.get(k, np.nan) for s in sessions]) for k in ks}
    mean_xc = {k: float(np.nanmean(v)) for k, v in xc.items()}
    n = len(sessions)

    def frac(cond) -> float:
        vals = [cond(s) for s in sessions if cond(s) is not None]
        return float(np.mean(vals)) if vals else math.nan

    g = [s.granger for s in sessions if s.granger]
    ish = [s.ishares for s in sessions if s.ishares]
    te = [s.te for s in sessions if s.te]
    lead_mass_pos = float(np.nansum([mean_xc[k] for k in ks if k > 0]))
    lead_mass_neg = float(np.nansum([mean_xc[k] for k in ks if k < 0]))
    return {
        "sessions": n,
        "xcorr_mean_by_lag_ms": {k * dt_ms: mean_xc[k] for k in ks},
        "xcorr_peak_lag_ms": int(max(mean_xc, key=lambda k: (mean_xc[k] if np.isfinite(mean_xc[k]) else -9)) * dt_ms),
        "lead_mass_es_leads": lead_mass_pos,
        "lead_mass_spy_leads": lead_mass_neg,
        "granger": {
            "n": len(g),
            "frac_es_to_spy_sig": float(np.mean([x["p_es_to_spy"] < 0.05 for x in g])) if g else math.nan,
            "frac_spy_to_es_sig": float(np.mean([x["p_spy_to_es"] < 0.05 for x in g])) if g else math.nan,
            "mean_coef_es_to_spy": float(np.mean([x["coef_es_to_spy"] for x in g])) if g else math.nan,
            "mean_coef_spy_to_es": float(np.mean([x["coef_spy_to_es"] for x in g])) if g else math.nan,
        },
        "info_shares": {
            "n": len(ish),
            "mean_gg_cs_es": float(np.mean([x["gg_cs_es"] for x in ish])) if ish else math.nan,
            "mean_hasbrouck_lower": float(np.mean([x["hasbrouck_is_es_lower"] for x in ish])) if ish else math.nan,
            "mean_hasbrouck_upper": float(np.mean([x["hasbrouck_is_es_upper"] for x in ish])) if ish else math.nan,
            "mean_alpha_es": float(np.mean([x["alpha_es"] for x in ish])) if ish else math.nan,
            "mean_alpha_spy": float(np.mean([x["alpha_spy"] for x in ish])) if ish else math.nan,
        },
        "transfer_entropy": {
            "n": len(te),
            "mean_net_te": float(np.mean([x["te_net"] for x in te])) if te else math.nan,
            "frac_sig": float(np.mean([x["p_value"] < 0.05 for x in te])) if te else math.nan,
        },
    }
