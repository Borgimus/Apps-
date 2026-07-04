"""CLI: python -m leadlag <command>

  selftest       validate every estimator on synthetic data with a planted lag
  estimate-cost  Databento cost estimate for the configured history (no download)
  fetch          download ES (Databento) + SPY (Alpaca) opening windows
  verify-mes     fetch a short MES sample and verify ES/MES redundancy
  run            run all phases on cached data → leadlag_results/REPORT.md
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys

import numpy as np

from .config import load_config

log = logging.getLogger("leadlag")


def _grids(cfg, store, days, min_needed: int = 10):
    from .data import load_grid

    grids = []
    for i, d in enumerate(days):
        g = load_grid(cfg, store, d)
        if g is not None:
            grids.append(g)
        if (i + 1) % 50 == 0:
            log.info("grids: %d/%d days", i + 1, len(days))
    if len(grids) < min_needed:
        print(f"only {len(grids)} usable sessions — fetch first?", file=sys.stderr)
    return grids


def cmd_selftest(args) -> int:
    from .stats import (SessionStats, aggregate, granger_bidirectional,
                        info_shares, transfer_entropy, xcorr_profile)
    from .strategy import run_combo
    from .synth import make_grid

    cfg = load_config()
    lag_ms = 200
    days = [dt.date(2025, 6, 2) + dt.timedelta(days=i) for i in range(8)]
    grids = [make_grid(cfg, d, seed=i, lag_ms=lag_ms, vol_bps_per_s=3.0)
             for i, d in enumerate(days)]
    lag_steps = [max(1, ms // cfg.grid["base_dt_ms"]) for ms in cfg.grid["xcorr_lags_ms"]]
    var_step = cfg.grid["var_dt_ms"] // cfg.grid["base_dt_ms"]

    sess = []
    for g in grids:
        xe, xs, m = g.x_es[::var_step], g.x_spy[::var_step], g.study[::var_step]
        sess.append(SessionStats(
            day=g.day,
            xcorr=xcorr_profile(g.x_es, g.x_spy, g.study, lag_steps),
            granger=granger_bidirectional(xe, xs, m, cfg.grid["var_lags"]),
            ishares=info_shares(xe, xs, m, 3),
            te=transfer_entropy(xe, xs, m, cfg.grid["te_bins"]),
        ))
    agg = aggregate(sess, lag_steps, cfg.grid["base_dt_ms"])
    peak = agg["xcorr_peak_lag_ms"]
    cs = agg["info_shares"]["mean_gg_cs_es"]
    gr = agg["granger"]
    te = agg["transfer_entropy"]["mean_net_te"]
    r_fast, _ = run_combo(cfg, grids, 500, 2, 5000, 0)      # latency < planted lag
    r_slow, _ = run_combo(cfg, grids, 500, 2, 5000, 1000)   # latency > planted lag
    checks = {
        f"xcorr peak at planted lag ({peak}ms vs {lag_ms}ms)": abs(peak - lag_ms) <= 100,
        f"GG component share → ES leader ({cs:.2f})": cs > 0.7,
        f"Granger ES→SPY dominant ({gr['frac_es_to_spy_sig']:.0%} vs {gr['frac_spy_to_es_sig']:.0%})":
            gr["frac_es_to_spy_sig"] > gr["frac_spy_to_es_sig"],
        f"net TE positive ({te:+.4f})": te > 0,
        f"edge at λ=0 ({np.mean(r_fast):+.1f} bps, n={len(r_fast)})": len(r_fast) > 20 and np.mean(r_fast) > 0,
        f"edge dead at λ>lag ({np.mean(r_slow):+.1f} bps)": len(r_slow) == 0 or np.mean(r_slow) < np.mean(r_fast) / 3,
    }
    for name, ok in checks.items():
        print(("✅" if ok else "❌"), name)
    print("SELFTEST", "PASS" if all(checks.values()) else "FAIL")
    return 0 if all(checks.values()) else 1


def cmd_estimate_cost(args) -> int:
    from .data import Store

    cfg = load_config()
    store = Store(cfg)
    days = store.sessions(cfg.history_start, dt.date.today())
    est = store.estimate_cost(days, sample=args.sample)
    print(json.dumps(est, indent=1))
    return 0


def cmd_fetch(args) -> int:
    from .data import Store

    cfg = load_config()
    store = Store(cfg)
    end = dt.date.fromisoformat(args.end) if args.end else dt.date.today()
    start = dt.date.fromisoformat(args.start) if args.start else cfg.history_start
    days = store.sessions(start, end)
    for i, d in enumerate(days):
        try:
            es = store.get_futures_trades(d)
            spy = store.get_equity_trades(d)
            if (i + 1) % 20 == 0:
                log.info("fetched %d/%d (%s: ES %s, SPY %s)", i + 1, len(days), d,
                         len(es) if es is not None else 0,
                         len(spy) if spy is not None else 0)
        except Exception as exc:
            log.error("day %s failed: %s", d, exc)
    return 0


def cmd_verify_mes(args) -> int:
    from .data import Store, session_grid

    cfg = load_config()
    store = Store(cfg)
    days = store.sessions(dt.date.today() - dt.timedelta(days=45), dt.date.today())[:20]
    cors = []
    for d in days:
        es = store.get_futures_trades(d)
        mes = store.get_futures_trades(d, symbol=cfg.fut_verify_symbol, tag="MES")
        if es is None or mes is None:
            continue
        g = session_grid(cfg, d, es, mes)   # MES in the "spy" slot
        ra, rb = np.diff(g.x_es), np.diff(g.x_spy)
        m = g.study[1:] & np.isfinite(ra) & np.isfinite(rb)
        if m.sum() > 1000:
            cors.append(float(np.corrcoef(ra[m], rb[m])[0, 1]))
    print(json.dumps({"days": len(cors),
                      "mean_corr_es_mes_50ms": float(np.mean(cors)) if cors else None}))
    return 0


def cmd_run(args) -> int:
    from .data import Store
    from .phases import phase2_conditioning, phase3_event_study, phase4_order_flow
    from .report import write_all
    from .stats import (SessionStats, aggregate, granger_bidirectional,
                        info_shares, transfer_entropy, xcorr_profile)
    from .strategy import phase5_ml, phase5_sweep, phase6_walk_forward

    cfg = load_config()
    store = Store(cfg)
    end = dt.date.fromisoformat(args.end) if args.end else dt.date.today()
    start = dt.date.fromisoformat(args.start) if args.start else cfg.history_start
    grids = _grids(cfg, store, store.sessions(start, end))
    if len(grids) < 10:
        return 1
    log.info("running phases on %d sessions", len(grids))

    lag_steps = [max(1, ms // cfg.grid["base_dt_ms"]) for ms in cfg.grid["xcorr_lags_ms"]]
    var_step = cfg.grid["var_dt_ms"] // cfg.grid["base_dt_ms"]
    sess = []
    for g in grids:
        xe, xs, m = g.x_es[::var_step], g.x_spy[::var_step], g.study[::var_step]
        sess.append(SessionStats(
            day=g.day,
            xcorr=xcorr_profile(g.x_es, g.x_spy, g.study, lag_steps),
            granger=granger_bidirectional(xe, xs, m, cfg.grid["var_lags"]),
            ishares=info_shares(xe, xs, m, 3),
            te=transfer_entropy(xe, xs, m, cfg.grid["te_bins"]),
        ))
    phase1 = aggregate(sess, lag_steps, cfg.grid["base_dt_ms"])
    log.info("phase1 done: peak lag %sms", phase1["xcorr_peak_lag_ms"])
    phase2 = phase2_conditioning(cfg, grids)
    log.info("phase2 done")
    phase3 = phase3_event_study(cfg, grids)
    log.info("phase3 done")
    phase4 = phase4_order_flow(cfg, grids)
    log.info("phase4 done")
    rows = phase5_sweep(cfg, grids)
    log.info("phase5 sweep done (%d combos)", len(rows))
    wf = [phase6_walk_forward(cfg, grids, lam) for lam in (100, 250, 500)]
    ml = phase5_ml(cfg, grids, latency_ms=100)
    log.info("phase6 done")

    # verdict per the directive's success criteria
    import math as _math
    best_realistic = max(
        (r for r in rows if r.latency_ms >= 100 and r.n_trades >= 100),
        key=lambda r: r.expectancy_bps if _math.isfinite(r.expectancy_bps) else -9e9,
        default=None)
    checks = {
        "es_leads_statistically": (phase1["xcorr_peak_lag_ms"] > 0
                                   and phase1["granger"]["frac_es_to_spy_sig"]
                                   > 2 * phase1["granger"]["frac_spy_to_es_sig"]
                                   and phase1["info_shares"]["mean_gg_cs_es"] > 0.6),
        "lead_visible_at_50ms_grid": phase1["lead_mass_es_leads"]
                                     > 3 * abs(phase1["lead_mass_spy_leads"]),
        "edge_exists_at_zero_latency": any(
            r.latency_ms == 0 and r.n_trades >= 100 and r.expectancy_bps > 0
            and r.t_stat > 2 for r in rows),
        "edge_survives_100ms_latency": best_realistic is not None
                                       and best_realistic.expectancy_bps > 0
                                       and best_realistic.t_stat > 2,
        "walk_forward_pf_gate": any(w.get("passes_pf_gate") for w in wf),
    }
    exploitable = checks["edge_survives_100ms_latency"] and checks["walk_forward_pf_gate"]
    verdict = {"checks": checks,
               "overall": ("EXPLOITABLE EDGE" if exploitable else
                           "ES LEADS, NOT EXPLOITABLE" if checks["es_leads_statistically"]
                           else "NO LEAD FOUND")}

    results = {
        "meta": {"sessions": len(grids), "first_day": str(grids[0].day),
                 "last_day": str(grids[-1].day)},
        "phase1": phase1, "phase2": phase2, "phase3": phase3, "phase4": phase4,
        "phase5_rows": rows, "phase5_ml": ml, "phase6": wf, "verdict": verdict,
    }
    path = write_all(cfg, results)
    print(f"\nVERDICT: {verdict['overall']}")
    for k, v in checks.items():
        print(("✅" if v else "❌"), k)
    print(f"report: {path}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="leadlag", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log", default="INFO")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("selftest")
    e = sub.add_parser("estimate-cost")
    e.add_argument("--sample", type=int, default=20)
    f = sub.add_parser("fetch")
    f.add_argument("--start", default="")
    f.add_argument("--end", default="")
    sub.add_parser("verify-mes")
    r = sub.add_parser("run")
    r.add_argument("--start", default="")
    r.add_argument("--end", default="")
    args = ap.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log.upper()),
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    return {"selftest": cmd_selftest, "estimate-cost": cmd_estimate_cost,
            "fetch": cmd_fetch, "verify-mes": cmd_verify_mes,
            "run": cmd_run}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
