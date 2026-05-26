"""
No-trade strategy readiness diagnostic.

Fetches today's 5-min bars for each enabled universe symbol, then evaluates
readiness for ORB, VWAP, and RSI_trend using the active config/settings.

No orders are placed.  No config is changed.  Read-only.

Usage:
    python scripts/readiness_check.py [--symbols SPY,QQQ] [--output-dir diagnostics]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import warnings
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.config.settings import get_settings
from app.data.yfinance_data import YFinanceDataSource
from app.scanning.universe_loader import UniverseLoader
from app.strategies.opening_range_breakout import OpeningRangeBreakoutStrategy
from app.strategies.rsi_trend_strategy import RSITrendStrategy
from app.strategies.vwap_strategy import VWAPReclaimStrategy

_ET = ZoneInfo("America/New_York")
logger = logging.getLogger("readiness_check")


def _parse_bar_interval_minutes(bar_interval: str) -> int:
    """'5m' → 5, '1m' → 1, '1h' → 60."""
    s = bar_interval.strip().lower()
    if s.endswith("h"):
        return int(s[:-1]) * 60
    if s.endswith("m"):
        return int(s[:-1])
    return 5


def _earliest_ready_time(min_bars: int, bar_interval_min: int, market_open: datetime) -> datetime:
    return market_open + timedelta(minutes=min_bars * bar_interval_min)


def _count_today_bars(bars: pd.DataFrame, today_date) -> int:
    """Count bars that belong to today's regular session (≥ 09:30 ET)."""
    if bars is None or bars.empty:
        return 0
    et_idx = bars.index.tz_convert(_ET)
    today_mask = pd.Series(et_idx.date, index=bars.index) == today_date
    session_mask = (et_idx.hour * 60 + et_idx.minute) >= (9 * 60 + 30)
    return int((today_mask & session_mask).sum())


async def run_readiness_check(
    symbols: list[str] | None = None,
    output_dir: str = "diagnostics",
) -> list[dict]:
    settings = get_settings()

    # Guard: never run with live trading
    if settings.live_trading_enabled:
        logger.error("LIVE_TRADING_ENABLED=true — readiness check is paper/research only. Aborting.")
        sys.exit(1)

    # ── Resolve symbol universe ───────────────────────────────────────────────
    if symbols:
        universe_symbols = {s.upper(): "cli_override" for s in symbols}
    else:
        loader = UniverseLoader()
        loader.load()
        enabled_groups = [g.strip() for g in settings.universe.groups_enabled.split(",")]
        universe_symbols = loader.get_symbols_with_groups(
            enabled_groups=enabled_groups,
            max_per_group=settings.universe.max_per_group,
            max_total=settings.universe.max_total_symbols,
            max_symbols=settings.universe.max_symbols_per_scan,
        )

    # ── Build strategies from current settings ────────────────────────────────
    rsi_cfg = settings.rsi_trend
    bar_interval_min = _parse_bar_interval_minutes(rsi_cfg.bar_interval)

    strategies = [
        OpeningRangeBreakoutStrategy(
            params={"range_minutes": 15, "min_range_pts": 0.5, "volume_confirmation": True}
        ),
        VWAPReclaimStrategy(
            params={"proximity_pct": 0.002, "confirmation_bars": 2}
        ),
        RSITrendStrategy(
            params={
                "rsi_period": rsi_cfg.rsi_period,
                "rsi_oversold": rsi_cfg.rsi_oversold,
                "rsi_overbought": rsi_cfg.rsi_overbought,
                "trend_ema_period": rsi_cfg.trend_ema_period,
                "bar_interval": rsi_cfg.bar_interval,
                "mode": rsi_cfg.mode,
            }
        ),
    ]

    now_et = datetime.now(tz=_ET)
    today_date = now_et.date()
    market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)

    logger.info(
        "RSI_trend config | rsi_period=%d | trend_ema_period=%d | min_bars=%d | bar_interval=%s | mode=%s",
        rsi_cfg.rsi_period, rsi_cfg.trend_ema_period, strategies[2].min_bars_required,
        rsi_cfg.bar_interval, rsi_cfg.mode,
    )
    logger.info("Fetching 5-min bars for %d symbols …", len(universe_symbols))

    # ── Fetch bars concurrently ───────────────────────────────────────────────
    data = YFinanceDataSource()

    async def _fetch_one(sym: str):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                bars = await data.get_intraday_bars(sym, interval="5m", days_back=3)
            return sym, bars, None
        except Exception as exc:
            logger.warning("Bar fetch failed for %s: %s", sym, exc)
            return sym, pd.DataFrame(), str(exc)

    fetch_tasks = [_fetch_one(sym) for sym in universe_symbols]
    bar_results = await asyncio.gather(*fetch_tasks)

    # ── Compute readiness ─────────────────────────────────────────────────────
    rows: list[dict] = []
    for sym, bars, err in bar_results:
        group = universe_symbols.get(sym, "unknown")
        today_bar_count = _count_today_bars(bars, today_date) if err is None else 0

        for strat in strategies:
            min_bars = strat.min_bars_required
            earliest_et = _earliest_ready_time(min_bars, bar_interval_min, market_open)

            if err:
                ready = False
                reason = f"bar fetch error: {err}"
                bars_available_today = 0
                bars_available_total = 0
            else:
                info = strat.get_readiness_info(bars)
                ready = info["ready"]
                reason = info["reason"]
                bars_available_today = today_bar_count
                bars_available_total = len(bars)

            rows.append({
                "symbol": sym,
                "group": group,
                "strategy_id": strat.strategy_id,
                "strategy_name": strat.name,
                "min_bars_required": min_bars,
                "bars_available_today": bars_available_today,
                "bars_available_total": bars_available_total,
                "ready": ready,
                "reason": reason,
                "earliest_ready_time_et": earliest_et.strftime("%H:%M"),
                "currently_past_ready_time": now_et >= earliest_et,
            })

    _write_report(rows, strategies, rsi_cfg, now_et, today_date, output_dir)
    _print_summary(rows, strategies, now_et)
    return rows


def _write_report(rows, strategies, rsi_cfg, now_et, today_date, output_dir: str):
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "strategy_readiness_snapshot.md"
    json_path = out_dir / "strategy_readiness_snapshot.json"

    bar_min = _parse_bar_interval_minutes(rsi_cfg.bar_interval)
    market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)

    lines = [
        "# Strategy Readiness Snapshot",
        "",
        f"**Generated:** {now_et.strftime('%Y-%m-%d %H:%M:%S ET')}  ",
        f"**Session date:** {today_date}  ",
        f"**Bar interval:** {rsi_cfg.bar_interval}  ",
        "",
        "## Active Configuration",
        "",
        "| Strategy | `strategy_id` | `min_bars_required` | Earliest Ready (ET) |",
        "|---|---|---|---|",
    ]

    for strat in strategies:
        earliest = _earliest_ready_time(strat.min_bars_required, bar_min, market_open)
        lines.append(
            f"| {strat.name} | `{strat.strategy_id}` | {strat.min_bars_required}"
            f" | {earliest.strftime('%H:%M')} |"
        )

    lines += [
        "",
        "### RSI_trend Active Parameters",
        "",
        "| Parameter | Value |",
        "|---|---|",
        f"| `rsi_period` | {rsi_cfg.rsi_period} |",
        f"| `rsi_oversold` | {rsi_cfg.rsi_oversold} |",
        f"| `rsi_overbought` | {rsi_cfg.rsi_overbought} |",
        f"| `trend_ema_period` | {rsi_cfg.trend_ema_period} |",
        f"| `bar_interval` | `{rsi_cfg.bar_interval}` |",
        f"| `mode` | `{rsi_cfg.mode}` |",
        "",
        "## Per-Symbol Readiness",
        "",
    ]

    for strat in strategies:
        strat_rows = [r for r in rows if r["strategy_id"] == strat.strategy_id]
        earliest = _earliest_ready_time(strat.min_bars_required, bar_min, market_open)
        n_ready = sum(1 for r in strat_rows if r["ready"])

        lines += [
            f"### {strat.name} (`{strat.strategy_id}`)",
            "",
            f"- `min_bars_required`: **{strat.min_bars_required}**",
            f"- Earliest ready: **{earliest.strftime('%H:%M ET')}**",
            f"- Currently ready: **{n_ready}/{len(strat_rows)} symbols**",
            "",
            "| Symbol | Group | Bars Today | Total Bars | Ready | Reason |",
            "|---|---|---|---|---|---|",
        ]

        for r in sorted(strat_rows, key=lambda x: (not x["ready"], x["symbol"])):
            icon = "✓" if r["ready"] else "✗"
            lines.append(
                f"| {r['symbol']} | {r['group']} | {r['bars_available_today']}"
                f" | {r['bars_available_total']} | {icon} | {r['reason']} |"
            )
        lines.append("")

    # Summary
    lines += [
        "## Summary",
        "",
    ]
    for strat in strategies:
        strat_rows = [r for r in rows if r["strategy_id"] == strat.strategy_id]
        n_ready = sum(1 for r in strat_rows if r["ready"])
        earliest = _earliest_ready_time(strat.min_bars_required, bar_min, market_open)
        lines.append(
            f"- **{strat.name}**: {n_ready}/{len(strat_rows)} symbols ready"
            f"  (earliest: {earliest.strftime('%H:%M ET')})"
        )

    lines += [
        "",
        "---",
        "",
        "*No orders placed. No config changed. Diagnostic read-only.*",
    ]

    md_path.write_text("\n".join(lines) + "\n")
    logger.info("Report written → %s", md_path)

    snapshot = {
        "generated_at": now_et.isoformat(),
        "session_date": str(today_date),
        "bar_interval": rsi_cfg.bar_interval,
        "rsi_trend_config": {
            "rsi_period": rsi_cfg.rsi_period,
            "rsi_oversold": float(rsi_cfg.rsi_oversold),
            "rsi_overbought": float(rsi_cfg.rsi_overbought),
            "trend_ema_period": rsi_cfg.trend_ema_period,
            "bar_interval": rsi_cfg.bar_interval,
            "mode": rsi_cfg.mode,
        },
        "strategy_configs": [
            {
                "strategy_id": s.strategy_id,
                "name": s.name,
                "min_bars_required": s.min_bars_required,
                "earliest_ready_time_et": _earliest_ready_time(
                    s.min_bars_required, bar_min, market_open
                ).strftime("%H:%M"),
            }
            for s in strategies
        ],
        "readiness": rows,
    }
    json_path.write_text(json.dumps(snapshot, indent=2, default=str))
    logger.info("JSON snapshot written → %s", json_path)


def _print_summary(rows, strategies, now_et):
    print(f"\n{'='*62}")
    print(f"Strategy Readiness — {now_et.strftime('%Y-%m-%d %H:%M ET')}")
    print(f"{'='*62}")

    bar_min = 5  # default; strategies all use 5m bars
    market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)

    for strat in strategies:
        strat_rows = [r for r in rows if r["strategy_id"] == strat.strategy_id]
        n_ready = sum(1 for r in strat_rows if r["ready"])
        earliest = _earliest_ready_time(strat.min_bars_required, bar_min, market_open)
        clock_past = now_et >= earliest

        status = "READY" if n_ready == len(strat_rows) else f"{n_ready}/{len(strat_rows)}"
        clock_note = "clock past ready time" if clock_past else f"clock: {earliest.strftime('%H:%M ET')} needed"

        print(f"\n  [{status:>7}] {strat.name}")
        print(f"            min_bars={strat.min_bars_required}  "
              f"earliest={earliest.strftime('%H:%M ET')}  ({clock_note})")

        not_ready = [r for r in strat_rows if not r["ready"]]
        ready_list = [r for r in strat_rows if r["ready"]]
        if ready_list:
            sample = [r["symbol"] for r in ready_list[:6]]
            print(f"            ✓ ready: {', '.join(sample)}" +
                  (f" +{len(ready_list)-6} more" if len(ready_list) > 6 else ""))
        for r in not_ready[:4]:
            print(f"            ✗ {r['symbol']:6s}: {r['reason']}")
        if len(not_ready) > 4:
            print(f"            … {len(not_ready)-4} more not ready")

    print(f"\n{'='*62}\n")


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="No-trade strategy readiness diagnostic")
    parser.add_argument(
        "--symbols",
        help="Comma-separated symbols (default: full enabled universe from config)",
    )
    parser.add_argument(
        "--output-dir",
        default="diagnostics",
        help="Directory for .md/.json report (default: diagnostics/)",
    )
    args = parser.parse_args()

    syms = (
        [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        if args.symbols
        else None
    )
    asyncio.run(run_readiness_check(symbols=syms, output_dir=args.output_dir))


if __name__ == "__main__":
    main()
