"""
Cumulative evaluation ledger — persisted as a JSON file.

Accumulates one LedgerEntry per session and recomputes cumulative statistics
after each update.

Schema (ledger.json):
  {
    "version": "1",
    "created_at": "<ISO>",
    "last_updated": "<ISO>",
    "sessions": [ <LedgerEntry>, ... ],
    "cumulative": { ... }
  }

Cumulative statistics:
  total_trading_days, total_trades, total_pnl, expectancy,
  win_rate, profit_factor, max_drawdown,
  pnl_by_strategy, pnl_by_entry_hour,
  pnl_by_delta_bucket, pnl_by_spread_bucket,
  reject_counts_by_reason
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_LEDGER_VERSION = "3"

# First session date for which ALL P1–P7 defect fixes are in effect.
# Sessions on or after this date are eligible for Phase 3 (clean cohort) analysis.
# Sessions before this date are Phase 1/2 engineering evidence only.
PHASE3_START = "2026-07-12"


# ── Data classes ──────────────────────────────────────────────────────────────


@dataclass
class LedgerEntry:
    date: str
    total_trades: int
    wins: int
    losses: int
    realized_pnl: float
    unrealized_pnl: float
    max_drawdown_session: float
    slippage_total: float
    spread_cost_total: float
    api_errors: int
    kill_switch_events: int
    trades_submitted: int
    trades_filled: int
    trades_cancelled: int
    trades_rejected: int
    by_strategy: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    by_entry_hour: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    by_delta_bucket: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    by_spread_bucket: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    reject_reasons: Dict[str, int] = field(default_factory=dict)
    data_clean: bool = False
    notes: Optional[str] = None
    # Phase 3 cohort fields (auto-set by add_session; backfilled manually for prior sessions)
    phase: str = "pre_phase3"
    contamination_flags: List[str] = field(default_factory=list)
    # Trade-level P&L components (used for accurate profit factor; absent in older entries)
    breakevens: int = 0
    gross_wins: float = 0.0    # sum of P&L for individual winning trades
    gross_losses: float = 0.0  # sum of abs(P&L) for individual losing trades
    trade_metrics_complete: Optional[bool] = None  # None means legacy, unverified coverage
    trade_metric_errors: List[str] = field(default_factory=list)


# ── Ledger class ──────────────────────────────────────────────────────────────


class EvaluationLedger:
    def __init__(self, ledger_file: str = "./evaluation/ledger.json"):
        self.ledger_file = Path(ledger_file)
        self.sessions: List[LedgerEntry] = []
        self._created_at: str = datetime.utcnow().isoformat()
        self._last_updated: str = self._created_at
        self._load_errors: List[str] = []

    # ── Persistence ───────────────────────────────────────────────────────────

    def save(self) -> None:
        self.ledger_file.parent.mkdir(parents=True, exist_ok=True)
        self._last_updated = datetime.utcnow().isoformat()
        data = {
            "version": _LEDGER_VERSION,
            "created_at": self._created_at,
            "last_updated": self._last_updated,
            "phase3_start_date": PHASE3_START,
            "load_errors": self._load_errors,
            "sessions": [asdict(s) for s in self.sessions],
            "cumulative": self.compute_cumulative(),
            "phase3_cumulative": self.compute_cumulative(phase3_only=True),
        }
        tmp = self.ledger_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str, allow_nan=False))
        tmp.replace(self.ledger_file)
        logger.info("Evaluation ledger saved to %s (%d session(s))", self.ledger_file, len(self.sessions))

    @classmethod
    def load(cls, ledger_file: str = "./evaluation/ledger.json") -> "EvaluationLedger":
        inst = cls(ledger_file=ledger_file)
        p = Path(ledger_file)
        if not p.exists():
            return inst
        try:
            raw = json.loads(p.read_text())
            inst._created_at = raw.get("created_at", inst._created_at)
            inst._last_updated = raw.get("last_updated", inst._last_updated)
            inst._load_errors = raw.get("load_errors", [])
            for s in raw.get("sessions", []):
                try:
                    inst.sessions.append(LedgerEntry(**s))
                except Exception as exc:
                    logger.warning("Skipping malformed ledger entry: %s", exc)
                    inst._load_errors.append(f"Malformed session {s.get('date', 'unknown')}")
        except Exception as exc:
            logger.error("Could not load ledger from %s: %s", ledger_file, exc)
            inst._load_errors.append("Ledger could not be parsed")
        return inst

    # ── Update ────────────────────────────────────────────────────────────────

    def add_session(self, report, trade_records: Optional[List] = None) -> LedgerEntry:
        """
        Add a DailyReport to the ledger.  trade_records is an optional list of
        raw DBTradeJournal ORM objects used for the per-hour / per-bucket
        breakdowns; omit if not available.
        """
        from app.evaluation.daily_report import DailyReport

        r: DailyReport = report
        wins = sum(s.wins for s in r.by_strategy)
        losses = sum(s.losses for s in r.by_strategy)

        # Per-strategy
        by_strategy: Dict[str, Dict] = {}
        for s in r.by_strategy:
            by_strategy[s.strategy_id] = {
                "trades": s.wins + s.losses,
                "wins": s.wins,
                "losses": s.losses,
                "pnl": s.realized_pnl,
            }

        # Per-hour, per-delta-bucket, per-spread-bucket, reject reasons
        by_hour: Dict[str, Dict] = {}
        by_delta: Dict[str, Dict] = {}
        by_spread: Dict[str, Dict] = {}
        reject_reasons: Dict[str, int] = {}
        breakevens = 0
        gross_wins = 0.0
        gross_losses = 0.0
        metric_errors = []
        complete = trade_records is not None
        if trade_records is None:
            metric_errors.append("Trade records not supplied")
        else:
            wins = losses = 0
            by_strategy = {}

        if trade_records:
            for t in trade_records:
                # Rejections remain diagnostic attempts, never completed trades.
                _accumulate_reject(t, reject_reasons)

                # Only broker-closed journal rows belong in performance metrics.
                # Rejected and cancelled attempts can carry a default 0.0 P&L;
                # treating those rows as breakevens corrupts trade count,
                # expectancy, win rate, and every cumulative dimension.
                if getattr(t, "status", "") != "closed":
                    continue
                _realized_pnl = getattr(t, "realized_pnl", None)
                if _realized_pnl is None:
                    metric_errors.append("Closed trade has no realized P&L")
                    continue
                if not math.isfinite(float(_realized_pnl)):
                    metric_errors.append("Closed trade has non-finite realized P&L")
                    continue

                _accumulate_hour(t, by_hour)
                _accumulate_delta(t, by_delta)
                _accumulate_spread(t, by_spread)

                # Accumulate per-trade P&L for trade-level profit factor.
                _pnl = float(_realized_pnl)
                sid = getattr(t, "strategy_id", None) or "unknown"
                bucket = by_strategy.setdefault(sid, {"trades": 0, "wins": 0, "losses": 0, "breakevens": 0, "pnl": 0.0})
                bucket["trades"] += 1
                bucket["pnl"] = round(bucket["pnl"] + _pnl, 2)
                if _pnl > 0:
                    wins += 1
                    bucket["wins"] += 1
                    gross_wins += _pnl
                elif _pnl < 0:
                    losses += 1
                    bucket["losses"] += 1
                    gross_losses += abs(_pnl)
                else:
                    breakevens += 1
                    bucket["breakevens"] += 1

        if trade_records is not None:
            if not math.isclose(gross_wins - gross_losses, r.realized_pnl, rel_tol=0, abs_tol=0.011):
                metric_errors.append("Trade P&L does not reconcile to daily report")
            if wins != sum(s.wins for s in r.by_strategy) or losses != sum(s.losses for s in r.by_strategy):
                metric_errors.append("Trade outcomes do not reconcile to daily report")
        complete = complete and not metric_errors

        phase = "phase3" if r.date >= PHASE3_START else "pre_phase3"
        entry = LedgerEntry(
            date=r.date,
            total_trades=wins + losses + breakevens,
            wins=wins,
            losses=losses,
            realized_pnl=r.realized_pnl,
            unrealized_pnl=r.unrealized_pnl,
            max_drawdown_session=r.max_drawdown,
            slippage_total=r.slippage_total,
            spread_cost_total=r.spread_cost_estimate,
            api_errors=r.api_errors,
            kill_switch_events=r.kill_switch_events,
            trades_submitted=r.trades_submitted,
            trades_filled=r.trades_filled,
            trades_cancelled=r.trades_cancelled,
            trades_rejected=r.trades_rejected,
            by_strategy=by_strategy,
            by_entry_hour=by_hour,
            by_delta_bucket=by_delta,
            by_spread_bucket=by_spread,
            reject_reasons=reject_reasons,
            phase=phase,
            breakevens=breakevens,
            gross_wins=round(gross_wins, 2),
            gross_losses=round(gross_losses, 2),
            trade_metrics_complete=complete,
            trade_metric_errors=metric_errors,
        )

        # Replace if same date already present
        self.sessions = [s for s in self.sessions if s.date != entry.date]
        self.sessions.append(entry)
        self.sessions.sort(key=lambda s: s.date)
        return entry

    # ── Cumulative stats ──────────────────────────────────────────────────────

    def compute_cumulative(self, phase3_only: bool = False) -> Dict[str, Any]:
        sessions = (
            [s for s in self.sessions if s.phase == "phase3"]
            if phase3_only
            else self.sessions
        )
        if not sessions:
            result = _empty_cumulative()
            if self._load_errors:
                result.update(trade_metrics_complete=False, total_trades=None,
                              expectancy=None, load_errors=self._load_errors)
            return result

        incomplete_dates = [s.date for s in sessions if not _metrics_complete(s)]
        complete = not incomplete_dates and not self._load_errors
        total_wins = sum(s.wins for s in sessions)
        total_losses = sum(s.losses for s in sessions)
        total_breakevens = sum(s.breakevens for s in sessions)
        total_trades = total_wins + total_losses + total_breakevens
        total_pnl = sum(s.realized_pnl for s in sessions)
        expectancy = (total_pnl / total_trades) if total_trades else 0.0
        win_rate = (total_wins / total_trades) if total_trades else None

        # Never mix partial trade coverage or session-level P&L with trade metrics.
        gw = sum(s.gross_wins for s in sessions)
        gl = sum(s.gross_losses for s in sessions)
        profit_factor = gw / gl if complete and gl > 0 else None

        # Max drawdown from cumulative PnL curve
        cum_pnl = 0.0
        peak = 0.0
        max_drawdown = 0.0
        for s in sorted(sessions, key=lambda x: x.date):
            cum_pnl += s.realized_pnl
            if cum_pnl > peak:
                peak = cum_pnl
            dd = peak - cum_pnl
            if dd > max_drawdown:
                max_drawdown = dd

        # Merge per-dimension dicts
        pnl_by_strategy = _merge_dimension(sessions, "by_strategy")
        pnl_by_hour = _merge_dimension(sessions, "by_entry_hour")
        pnl_by_delta = _merge_dimension(sessions, "by_delta_bucket")
        pnl_by_spread = _merge_dimension(sessions, "by_spread_bucket")

        # Reject reasons
        reject_counts: Dict[str, int] = {}
        for s in sessions:
            for reason, cnt in s.reject_reasons.items():
                reject_counts[reason] = reject_counts.get(reason, 0) + cnt

        return {
            "trading_days": len(sessions),
            "total_trades": total_trades if complete else None,
            "reported_total_trades": sum(s.total_trades for s in sessions),
            "trade_metrics_complete": complete,
            "incomplete_session_dates": incomplete_dates,
            "load_errors": self._load_errors,
            "total_pnl": round(total_pnl, 2),
            "expectancy": round(expectancy, 2) if complete else None,
            "win_rate": round(win_rate, 4) if complete and win_rate is not None else None,
            "profit_factor": round(profit_factor, 2) if profit_factor is not None else None,
            "max_drawdown": round(max_drawdown, 2),
            "pnl_by_strategy": pnl_by_strategy,
            "pnl_by_entry_hour": pnl_by_hour,
            "pnl_by_delta_bucket": pnl_by_delta,
            "pnl_by_spread_bucket": pnl_by_spread,
            "reject_counts_by_reason": reject_counts,
        }


# ── Helpers ───────────────────────────────────────────────────────────────────


def _empty_cumulative() -> Dict[str, Any]:
    return {
        "trade_metrics_complete": True,
        "reported_total_trades": 0,
        "incomplete_session_dates": [],
        "load_errors": [],
        "trading_days": 0,
        "total_trades": 0,
        "total_pnl": 0.0,
        "expectancy": 0.0,
        "win_rate": None,
        "profit_factor": None,
        "max_drawdown": 0.0,
        "pnl_by_strategy": {},
        "pnl_by_entry_hour": {},
        "pnl_by_delta_bucket": {},
        "pnl_by_spread_bucket": {},
        "reject_counts_by_reason": {},
    }


def _metrics_complete(s: LedgerEntry) -> bool:
    return (
        s.trade_metrics_complete is True
        and not s.trade_metric_errors
        and s.total_trades == s.wins + s.losses + s.breakevens
        and s.gross_wins >= 0 and s.gross_losses >= 0
        and math.isclose(s.gross_wins - s.gross_losses, s.realized_pnl, rel_tol=0, abs_tol=0.011)
        and (s.wins == 0) == (s.gross_wins == 0)
        and (s.losses == 0) == (s.gross_losses == 0)
    )


def _merge_dimension(sessions: List[LedgerEntry], attr: str) -> Dict[str, Dict]:
    merged: Dict[str, Dict] = {}
    for s in sessions:
        for key, val in getattr(s, attr, {}).items():
            if key not in merged:
                merged[key] = {"trades": 0, "wins": 0, "losses": 0, "breakevens": 0, "pnl": 0.0}
            merged[key]["trades"] += val.get("trades", 0)
            merged[key]["wins"] += val.get("wins", 0)
            merged[key]["losses"] += val.get("losses", 0)
            merged[key]["breakevens"] += val.get("breakevens", 0)
            merged[key]["pnl"] = round(merged[key]["pnl"] + val.get("pnl", 0.0), 2)
    return merged


def _delta_bucket(delta: Optional[float]) -> str:
    if delta is None:
        return "unknown"
    d = abs(delta)  # puts have negative delta; bucket by magnitude
    if d < 0.30:
        return "low (<0.30)"
    if d < 0.40:
        return "mid (0.30-0.40)"
    if d < 0.50:
        return "target (0.40-0.50)"
    return "high (>0.50)"


def _spread_bucket(spread_pct: Optional[float]) -> str:
    if spread_pct is None:
        return "unknown"
    if spread_pct < 0.05:
        return "tight (<5%)"
    if spread_pct < 0.10:
        return "moderate (5-10%)"
    return "wide (>10%)"


def _accumulate_hour(trade, by_hour: Dict) -> None:
    entry_time = getattr(trade, "entry_time", None)
    pnl = getattr(trade, "realized_pnl", None)
    if entry_time is None or pnl is None:
        return
    ts = entry_time
    if hasattr(ts, "astimezone"):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=ZoneInfo("America/New_York"))
        ts = ts.astimezone(ZoneInfo("America/New_York"))
    hour_key = ts.strftime("%H:00")
    if hour_key not in by_hour:
        by_hour[hour_key] = {"trades": 0, "wins": 0, "losses": 0, "breakevens": 0, "pnl": 0.0}
    by_hour[hour_key]["trades"] += 1
    pnl_f = float(pnl)
    if pnl_f > 0:
        by_hour[hour_key]["wins"] += 1
    elif pnl_f < 0:
        by_hour[hour_key]["losses"] += 1
    else:
        by_hour[hour_key]["breakevens"] += 1
    by_hour[hour_key]["pnl"] = round(by_hour[hour_key]["pnl"] + pnl_f, 2)


def _accumulate_delta(trade, by_delta: Dict) -> None:
    delta = getattr(trade, "delta", None)
    pnl = getattr(trade, "realized_pnl", None)
    if pnl is None:
        return
    bucket = _delta_bucket(delta)
    if bucket not in by_delta:
        by_delta[bucket] = {"trades": 0, "wins": 0, "losses": 0, "breakevens": 0, "pnl": 0.0}
    by_delta[bucket]["trades"] += 1
    pnl_f = float(pnl)
    if pnl_f > 0:
        by_delta[bucket]["wins"] += 1
    elif pnl_f < 0:
        by_delta[bucket]["losses"] += 1
    else:
        by_delta[bucket]["breakevens"] += 1
    by_delta[bucket]["pnl"] = round(by_delta[bucket]["pnl"] + pnl_f, 2)


def _accumulate_spread(trade, by_spread: Dict) -> None:
    spread_pct = getattr(trade, "spread_pct", None)
    pnl = getattr(trade, "realized_pnl", None)
    if pnl is None:
        return
    bucket = _spread_bucket(spread_pct)
    if bucket not in by_spread:
        by_spread[bucket] = {"trades": 0, "wins": 0, "losses": 0, "breakevens": 0, "pnl": 0.0}
    by_spread[bucket]["trades"] += 1
    pnl_f = float(pnl)
    if pnl_f > 0:
        by_spread[bucket]["wins"] += 1
    elif pnl_f < 0:
        by_spread[bucket]["losses"] += 1
    else:
        by_spread[bucket]["breakevens"] += 1
    by_spread[bucket]["pnl"] = round(by_spread[bucket]["pnl"] + pnl_f, 2)


def _accumulate_reject(trade, reject_reasons: Dict) -> None:
    status = getattr(trade, "status", "")
    if status != "rejected":
        return
    reason = getattr(trade, "rejection_reason", None) or "unknown"
    # Truncate long reasons to a canonical label
    reason_key = reason.split(":")[0].strip()[:64]
    reject_reasons[reason_key] = reject_reasons.get(reason_key, 0) + 1
