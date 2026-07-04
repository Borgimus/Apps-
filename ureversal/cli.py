"""CLI: python -m ureversal <command>

Commands
  selftest   validate the whole pipeline on synthetic data (no keys needed)
  fetch      download + cache historical 1s data for a date range
  research   run the §8 statistical validation study → report + JSON
  backtest   run the backtester with current yaml params
  optimize   walk-forward parameter optimization
  replay     replay one historical session through the live code path
  scan       real-time scanner (signals only, no orders)
  trade      live loop (paper unless live.mode=live AND LIVE_TRADING_ENABLED=true)
  dashboard  performance dashboard web UI
  reset-risk clear the consecutive-loss circuit breaker
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
from pathlib import Path

from .config import load_config

log = logging.getLogger("ureversal")


def _dates(args) -> tuple[dt.date, dt.date]:
    return dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)


def _load_sessions(cfg, start: dt.date, end: dt.date) -> dict:
    from .data import DataStore

    ds = DataStore(cfg)
    sessions = {}
    days = ds.sessions(start, end)
    for i, d in enumerate(days):
        s = ds.get_session(d)
        if s is not None:
            sessions[d] = s
        if (i + 1) % 20 == 0:
            log.info("loaded %d/%d days", i + 1, len(days))
    log.info("%d tradable sessions in range", len(sessions))
    return sessions


def cmd_selftest(args) -> int:
    """Synthetic end-to-end validation: detector finds planted patterns, takes
    ~no trades on pattern-free sessions, and live/batch paths agree."""
    import pandas as pd

    from .backtest import Backtester
    from .replay import replay_session
    from .synth import default_planted_u, make_session

    cfg = load_config()
    days = pd.bdate_range(dt.date(2025, 3, 3), periods=30)
    planted, control = [], []
    for i, d in enumerate(days):
        u = default_planted_u(offset_s=400 + i * 13, lead_s=8, depth_bps=30)
        planted.append((d.date(), make_session(d.date(), seed=i, vol_bps_per_s=0.35,
                                               plant=[u]).bars))
        control.append((d.date(), make_session(d.date(), seed=900 + i,
                                               vol_bps_per_s=0.35).bars))
    bt = Backtester(cfg)
    mp = bt.run(planted).metrics
    mc = bt.run(control).metrics
    day0, bars0 = planted[0]
    rp = replay_session(cfg, bars0, day0)
    ok = mp.n_trades >= 8 and mp.win_rate > 0.7 and mc.n_trades <= 2 and rp.parity_ok
    print(f"planted: {mp.n_trades} trades, win {mp.win_rate:.2f}, "
          f"exp {mp.expectancy_bps:.1f} bps | control: {mc.n_trades} trades | "
          f"live/batch parity: {rp.parity_ok}")
    print("SELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def cmd_fetch(args) -> int:
    cfg = load_config()
    _load_sessions(cfg, *_dates(args))
    return 0


def cmd_research(args) -> int:
    from .research import render_report, run_study

    cfg = load_config()
    sessions = _load_sessions(cfg, *_dates(args))
    if not sessions:
        print("no sessions loaded — check keys/feed/date range", file=sys.stderr)
        return 1
    res = run_study(cfg, sessions)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_report(res))
    out.with_suffix(".json").write_text(json.dumps(res, indent=1, default=str))
    print(render_report(res))
    print(f"\nwritten: {out} and {out.with_suffix('.json')}")
    return 0


def cmd_backtest(args) -> int:
    from .backtest import Backtester
    from .viz import plot_equity

    cfg = load_config()
    sessions = _load_sessions(cfg, *_dates(args))
    bt = Backtester(cfg)
    res = bt.run(sessions.items())
    m = res.metrics
    print(json.dumps(m.as_dict(), indent=1, default=str))
    if args.plot:
        p = plot_equity(res.daily_pnl, bt.equity, Path(args.plot))
        print(f"equity curve: {p}")
    return 0


def cmd_optimize(args) -> int:
    from .optimize import walk_forward

    cfg = load_config()
    sessions = _load_sessions(cfg, *_dates(args))
    wf = walk_forward(cfg, sessions, budget=args.budget)
    print("\nchosen parameters per fold (stability check):")
    print(wf.parameter_stability().to_string())
    print("\npooled out-of-sample metrics:")
    print(json.dumps(wf.oos_metrics.as_dict(), indent=1, default=str))
    return 0


def cmd_replay(args) -> int:
    from .replay import replay_date
    from .viz import plot_session

    cfg = load_config()
    day = dt.date.fromisoformat(args.date)
    r = replay_date(cfg, day)
    if r is None:
        return 1
    print(f"{day}: {len(r.triggers)} triggers, {len(r.trades)} trades, "
          f"parity_ok={r.parity_ok}")
    for t in r.trades:
        print(f"  {t.entry_ts.time()} → {t.exit_ts.time()} {t.net_ret_bps:+.1f} bps "
              f"({t.exit_reason})")
    if args.plot:
        p = plot_session(cfg, r.bars, day, Path(args.plot),
                         triggers=r.triggers, trades=r.trades)
        print(f"chart: {p}")
    return 0 if r.parity_ok else 1


def cmd_scan(args) -> int:
    from .live import run_live

    run_live(load_config(), execute=False)
    return 0


def cmd_trade(args) -> int:
    from .live import run_live

    cfg = load_config()
    if cfg.live["mode"] == "live" and cfg.live_trading_enabled:
        print("*** LIVE TRADING MODE — real orders in 5s, Ctrl-C to abort ***")
        import time

        time.sleep(5)
    run_live(cfg, execute=True)
    return 0


def cmd_dashboard(args) -> int:
    from .dashboard import run_dashboard

    run_dashboard(load_config())
    return 0


def cmd_reset_risk(args) -> int:
    from .risk import RiskManager

    RiskManager(load_config()).manual_reset()
    print("circuit breaker cleared")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ureversal", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log", default="INFO")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("selftest")
    for name in ("fetch", "research", "backtest", "optimize"):
        s = sub.add_parser(name)
        s.add_argument("--start", required=True)
        s.add_argument("--end", required=True)
        if name == "research":
            s.add_argument("--out", default="ureversal_results/validation_report.md")
        if name == "backtest":
            s.add_argument("--plot", default="")
        if name == "optimize":
            s.add_argument("--budget", type=int, default=60)
    r = sub.add_parser("replay")
    r.add_argument("--date", required=True)
    r.add_argument("--plot", default="")
    sub.add_parser("scan")
    sub.add_parser("trade")
    sub.add_parser("dashboard")
    sub.add_parser("reset-risk")

    args = ap.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log.upper()),
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    return {
        "selftest": cmd_selftest, "fetch": cmd_fetch, "research": cmd_research,
        "backtest": cmd_backtest, "optimize": cmd_optimize, "replay": cmd_replay,
        "scan": cmd_scan, "trade": cmd_trade, "dashboard": cmd_dashboard,
        "reset-risk": cmd_reset_risk,
    }[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
