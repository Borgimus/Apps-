"""
Daily evaluation report — built from DBTradeJournal and DBSessionLog rows
for a single session date.

Outputs:
  to_json(report)     → JSON string
  to_markdown(report) → Markdown string
  send_summary_alert  → sends to AlertService if configured
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")

# ── Data classes ──────────────────────────────────────────────────────────────


@dataclass
class StrategyStats:
    strategy_id: str
    signals: int = 0
    submitted: int = 0
    fills: int = 0
    cancels: int = 0
    rejects: int = 0
    realized_pnl: float = 0.0
    wins: int = 0
    losses: int = 0
    win_rate: Optional[float] = None
    avg_win: Optional[float] = None
    avg_loss: Optional[float] = None


@dataclass
class DailyReport:
    date: str
    session_start: Optional[str]
    session_end: Optional[str]

    # Signal / trade counts
    total_signals: int = 0
    trades_submitted: int = 0
    trades_filled: int = 0
    trades_cancelled: int = 0
    trades_rejected: int = 0

    # PnL
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0

    # Performance
    win_rate: Optional[float] = None
    avg_win: Optional[float] = None
    avg_loss: Optional[float] = None
    max_drawdown: float = 0.0
    largest_win: Optional[float] = None
    largest_loss: Optional[float] = None

    # Cost analysis
    slippage_total: float = 0.0
    spread_cost_estimate: float = 0.0

    # Fill efficiency
    fill_rate: Optional[float] = None           # fills / submitted
    cancel_rate: Optional[float] = None         # cancels / submitted
    missed_fills_count: int = 0                 # cancelled without any fill
    time_to_fill_avg_secs: Optional[float] = None
    avg_spread_at_entry: Optional[float] = None
    avg_spread_at_exit: Optional[float] = None

    # Exit-reason breakdown (fraction of filled trades)
    stop_loss_hit_pct: Optional[float] = None
    take_profit_hit_pct: Optional[float] = None
    eod_exit_pct: Optional[float] = None

    # System health
    api_errors: int = 0
    kill_switch_events: int = 0

    # Per-strategy breakdown
    by_strategy: List[StrategyStats] = field(default_factory=list)

    # Auto-generated notes
    notes: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)


# ── Builder ───────────────────────────────────────────────────────────────────


async def build_daily_report(db_session, session_date: str, settings=None) -> DailyReport:
    """Query the DB for session_date and compute the full evaluation report."""
    from sqlalchemy import func, select
    from app.api.models import DBSessionLog, DBSignal, DBTradeJournal

    report = DailyReport(date=session_date, session_start=None, session_end=None)

    # ── Session start / end from session logs ─────────────────────────────────
    logs = (
        await db_session.execute(
            select(DBSessionLog)
            .where(DBSessionLog.session_date == session_date)
            .order_by(DBSessionLog.timestamp)
        )
    ).scalars().all()

    if logs:
        first_ts = logs[0].timestamp
        last_ts = logs[-1].timestamp
        report.session_start = _fmt_ts(first_ts)
        report.session_end = _fmt_ts(last_ts)

    # ── API errors & kill switch events ──────────────────────────────────────
    for log in logs:
        if log.level == "error":
            report.api_errors += 1
        evt = (log.event or "").lower()
        if "kill_switch" in evt or "kill switch" in evt:
            report.kill_switch_events += 1

    # ── Signal counts ─────────────────────────────────────────────────────────
    signal_rows = (
        await db_session.execute(
            select(DBSignal).where(
                func.date(DBSignal.timestamp) == session_date
            )
        )
    ).scalars().all()
    report.total_signals = len(signal_rows)
    signals_by_strategy: Dict[str, int] = {}
    for s in signal_rows:
        signals_by_strategy[s.strategy_id] = signals_by_strategy.get(s.strategy_id, 0) + 1

    # ── Trade journal ─────────────────────────────────────────────────────────
    trades = (
        await db_session.execute(
            select(DBTradeJournal).where(DBTradeJournal.session_date == session_date)
        )
    ).scalars().all()

    submitted = [t for t in trades if t.status != "rejected"]
    fills = [t for t in trades if t.fill_price is not None and t.status in ("closed", "open", "cancelled")]
    closed = [t for t in trades if t.status == "closed" and t.realized_pnl is not None]
    cancelled = [t for t in trades if t.status == "cancelled"]
    rejected = [t for t in trades if t.status == "rejected"]

    report.trades_submitted = len(submitted)
    report.trades_filled = len(fills)
    report.trades_cancelled = len(cancelled)
    report.trades_rejected = len(rejected)

    # ── PnL ───────────────────────────────────────────────────────────────────
    pnls = [float(t.realized_pnl) for t in closed]
    report.realized_pnl = sum(pnls)
    report.unrealized_pnl = sum(
        float(t.unrealized_pnl) for t in trades
        if t.status == "open" and t.unrealized_pnl is not None
    )

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    if pnls:
        report.win_rate = len(wins) / len(pnls)
    if wins:
        report.avg_win = sum(wins) / len(wins)
        report.largest_win = max(wins)
    if losses:
        report.avg_loss = sum(losses) / len(losses)
        report.largest_loss = min(losses)

    # ── Max drawdown from cumulative PnL curve ────────────────────────────────
    if closed:
        ordered = sorted(closed, key=lambda t: t.exit_time or datetime.min)
        cum = 0.0
        peak = 0.0
        max_dd = 0.0
        for t in ordered:
            cum += float(t.realized_pnl)
            if cum > peak:
                peak = cum
            dd = peak - cum
            if dd > max_dd:
                max_dd = dd
        report.max_drawdown = max_dd

    # ── Slippage & spread cost ────────────────────────────────────────────────
    report.slippage_total = sum(
        float(t.slippage) for t in fills if t.slippage is not None
    )
    report.spread_cost_estimate = sum(
        _spread_cost(t) for t in fills
    )

    # ── Fill efficiency metrics ───────────────────────────────────────────────
    if report.trades_submitted > 0:
        report.fill_rate = report.trades_filled / report.trades_submitted
        report.cancel_rate = report.trades_cancelled / report.trades_submitted

    report.missed_fills_count = sum(
        1 for t in cancelled if not t.fill_price
    )

    entry_spreads = [float(t.spread_pct) for t in submitted if t.spread_pct is not None]
    if entry_spreads:
        report.avg_spread_at_entry = sum(entry_spreads) / len(entry_spreads)

    exit_spreads = [
        float(t.exit_spread_pct) for t in closed
        if getattr(t, "exit_spread_pct", None) is not None
    ]
    if exit_spreads:
        report.avg_spread_at_exit = sum(exit_spreads) / len(exit_spreads)

    fill_times = [
        float(t.time_to_fill_secs) for t in closed
        if getattr(t, "time_to_fill_secs", None) is not None
    ]
    if fill_times:
        report.time_to_fill_avg_secs = sum(fill_times) / len(fill_times)

    # ── Exit-reason breakdown ─────────────────────────────────────────────────
    if closed:
        n = len(closed)
        report.stop_loss_hit_pct = sum(
            1 for t in closed if t.exit_reason == "stop_loss"
        ) / n
        report.take_profit_hit_pct = sum(
            1 for t in closed if t.exit_reason == "take_profit"
        ) / n
        report.eod_exit_pct = sum(
            1 for t in closed if t.exit_reason == "eod_exit"
        ) / n

    # ── Per-strategy breakdown ────────────────────────────────────────────────
    strat_ids = {t.strategy_id for t in trades} | set(signals_by_strategy)
    for sid in sorted(strat_ids):
        strat_trades = [t for t in trades if t.strategy_id == sid]
        strat_closed = [t for t in strat_trades if t.status == "closed" and t.realized_pnl is not None]
        strat_pnls = [float(t.realized_pnl) for t in strat_closed]
        strat_wins = [p for p in strat_pnls if p > 0]
        strat_losses = [p for p in strat_pnls if p < 0]

        ss = StrategyStats(
            strategy_id=sid,
            signals=signals_by_strategy.get(sid, 0),
            submitted=len([t for t in strat_trades if t.status != "rejected"]),
            fills=len([t for t in strat_trades if t.fill_price is not None and t.status in ("closed", "open", "cancelled")]),
            cancels=len([t for t in strat_trades if t.status == "cancelled"]),
            rejects=len([t for t in strat_trades if t.status == "rejected"]),
            realized_pnl=sum(strat_pnls),
            wins=len(strat_wins),
            losses=len(strat_losses),
        )
        if strat_pnls:
            ss.win_rate = len(strat_wins) / len(strat_pnls)
        if strat_wins:
            ss.avg_win = sum(strat_wins) / len(strat_wins)
        if strat_losses:
            ss.avg_loss = sum(strat_losses) / len(strat_losses)
        report.by_strategy.append(ss)

    # ── Notes & recommendations ───────────────────────────────────────────────
    report.notes, report.recommendations = _generate_notes(report)

    return report


def _spread_cost(trade) -> float:
    bid = getattr(trade, "bid", None)
    ask = getattr(trade, "ask", None)
    qty = getattr(trade, "filled_quantity", None) or getattr(trade, "quantity", 1)
    if bid is not None and ask is not None:
        return float((ask - bid) / 2 * qty * 100)
    return 0.0


def _fmt_ts(ts: Optional[datetime]) -> Optional[str]:
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=_ET)
    return ts.astimezone(_ET).strftime("%Y-%m-%d %H:%M:%S %Z")


def _generate_notes(r: DailyReport):
    notes: List[str] = []
    recs: List[str] = []

    total_closed = len([s for s in r.by_strategy for _ in range(s.wins + s.losses)])
    total_attempted = r.trades_submitted + r.trades_rejected

    if total_attempted > 0:
        rej_pct = r.trades_rejected / total_attempted * 100
        if rej_pct > 50:
            notes.append(f"{rej_pct:.0f}% of signals were rejected ({r.trades_rejected}/{total_attempted})")
            recs.append("Review signal filters — high rejection rate may indicate overly strict criteria or poor market fit")

    if r.win_rate is not None:
        if r.win_rate < 0.40:
            notes.append(f"Win rate {r.win_rate:.0%} is below 40%")
            recs.append("Win rate below 40%; consider tightening entry criteria or adjusting profit/stop targets")
        elif r.win_rate > 0.70:
            notes.append(f"Win rate {r.win_rate:.0%} is high — verify targets are not too conservative")

    if r.realized_pnl < 0:
        notes.append(f"Net loss session: ${r.realized_pnl:.2f}")
    elif r.realized_pnl > 0:
        notes.append(f"Net profit session: ${r.realized_pnl:.2f}")

    if r.max_drawdown > 200:
        notes.append(f"Max intraday drawdown: ${r.max_drawdown:.2f}")
        recs.append("Max drawdown exceeded $200 — review stop-loss settings")

    if r.api_errors > 0:
        notes.append(f"{r.api_errors} API error(s) recorded during session")
        recs.append("Investigate API errors in logs — persistent errors may affect fill accuracy")

    if r.kill_switch_events > 0:
        notes.append(f"Kill switch was activated {r.kill_switch_events} time(s)")
        recs.append("Investigate what triggered the kill switch")

    slippage_per_fill = (r.slippage_total / r.trades_filled) if r.trades_filled > 0 else 0
    if abs(slippage_per_fill) > 0.10:
        notes.append(f"Average slippage: ${slippage_per_fill:.3f}/contract")
        recs.append("Average slippage > $0.10/contract — consider adjusting limit price offset")

    if not notes:
        notes.append("Session completed without notable issues")

    return notes, recs


# ── Output formatters ─────────────────────────────────────────────────────────


def to_json(report: DailyReport) -> str:
    return json.dumps(asdict(report), indent=2, default=str)


def to_markdown(report: DailyReport) -> str:
    r = report
    win_rate_str = f"{r.win_rate:.1%}" if r.win_rate is not None else "n/a"
    avg_win_str = f"${r.avg_win:.2f}" if r.avg_win is not None else "n/a"
    avg_loss_str = f"${r.avg_loss:.2f}" if r.avg_loss is not None else "n/a"
    largest_win_str = f"${r.largest_win:.2f}" if r.largest_win is not None else "n/a"
    largest_loss_str = f"${r.largest_loss:.2f}" if r.largest_loss is not None else "n/a"

    strat_rows = ""
    for s in r.by_strategy:
        wr = f"{s.win_rate:.1%}" if s.win_rate is not None else "n/a"
        strat_rows += (
            f"| {s.strategy_id} | {s.signals} | {s.submitted} | {s.fills} | "
            f"{s.cancels} | {s.rejects} | ${s.realized_pnl:.2f} | {wr} |\n"
        )

    notes_md = "\n".join(f"- {n}" for n in r.notes) or "- None"
    recs_md = "\n".join(f"- {rc}" for rc in r.recommendations) or "- None"

    return f"""# Daily Evaluation Report — {r.date}

**Session:** {r.session_start or "unknown"} → {r.session_end or "unknown"}

## Trade Summary

| Metric | Value |
|---|---|
| Total signals | {r.total_signals} |
| Trades submitted | {r.trades_submitted} |
| Fills | {r.trades_filled} |
| Cancels | {r.trades_cancelled} |
| Rejects | {r.trades_rejected} |

## PnL

| Metric | Value |
|---|---|
| Realized PnL | ${r.realized_pnl:.2f} |
| Unrealized PnL | ${r.unrealized_pnl:.2f} |
| Win rate | {win_rate_str} |
| Avg win | {avg_win_str} |
| Avg loss | {avg_loss_str} |
| Largest win | {largest_win_str} |
| Largest loss | {largest_loss_str} |
| Max drawdown | ${r.max_drawdown:.2f} |

## Cost Analysis

| Metric | Value |
|---|---|
| Slippage total | ${r.slippage_total:.2f} |
| Spread cost estimate | ${r.spread_cost_estimate:.2f} |
| Avg spread at entry | {f"{r.avg_spread_at_entry:.1%}" if r.avg_spread_at_entry is not None else "n/a"} |
| Avg spread at exit | {f"{r.avg_spread_at_exit:.1%}" if r.avg_spread_at_exit is not None else "n/a"} |

## Fill Efficiency

| Metric | Value |
|---|---|
| Fill rate | {f"{r.fill_rate:.1%}" if r.fill_rate is not None else "n/a"} |
| Cancel rate | {f"{r.cancel_rate:.1%}" if r.cancel_rate is not None else "n/a"} |
| Missed fills | {r.missed_fills_count} |
| Avg time to fill | {f"{r.time_to_fill_avg_secs:.0f}s" if r.time_to_fill_avg_secs is not None else "n/a"} |
| Stop-loss exits | {f"{r.stop_loss_hit_pct:.1%}" if r.stop_loss_hit_pct is not None else "n/a"} |
| Take-profit exits | {f"{r.take_profit_hit_pct:.1%}" if r.take_profit_hit_pct is not None else "n/a"} |
| EOD exits | {f"{r.eod_exit_pct:.1%}" if r.eod_exit_pct is not None else "n/a"} |

## System Health

| Metric | Value |
|---|---|
| API errors | {r.api_errors} |
| Kill switch events | {r.kill_switch_events} |

## Per-Strategy Breakdown

| Strategy | Signals | Submitted | Fills | Cancels | Rejects | PnL | Win Rate |
|---|---|---|---|---|---|---|---|
{strat_rows.rstrip()}

## Notes

{notes_md}

## Recommendations

{recs_md}
"""


async def send_summary_alert(report: DailyReport, alert_service) -> None:
    """Send a one-line session summary via AlertService."""
    if alert_service is None:
        return
    try:
        from app.utils.alerting import AlertEvent
        win_rate_str = f"{report.win_rate:.0%}" if report.win_rate is not None else "n/a"
        await alert_service.send(
            AlertEvent.SESSION_SUMMARY,
            (
                f"Eval report {report.date} | "
                f"trades={report.trades_filled} | "
                f"pnl=${report.realized_pnl:.2f} | "
                f"win={win_rate_str} | "
                f"dd=${report.max_drawdown:.2f}"
            ),
            data={
                "date": report.date,
                "trades_filled": report.trades_filled,
                "realized_pnl": report.realized_pnl,
                "win_rate": report.win_rate,
                "max_drawdown": report.max_drawdown,
                "api_errors": report.api_errors,
            },
        )
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Could not send evaluation summary alert: %s", exc)
