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
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_LEDGER_VERSION = "1"


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


# ── Ledger class ──────────────────────────────────────────────────────────────


class EvaluationLedger:
    def __init__(self, ledger_file: str = "./evaluation/ledger.json"):
        self.ledger_file = Path(ledger_file)
        self.sessions: List[LedgerEntry] = []
        self._created_at: str = datetime.utcnow().isoformat()
        self._last_updated: str = self._created_at

    # ── Persistence ───────────────────────────────────────────────────────────

    def save(self) -> None:
        self.ledger_file.parent.mkdir(parents=True, exist_ok=True)
        self._last_updated = datetime.utcnow().isoformat()
        data = {
            "version": _LEDGER_VERSION,
            "created_at": self._created_at,
            "last_updated": self._last_updated,
            "sessions": [asdict(s) for s in self.sessions],
            "cumulative": self.compute_cumulative(),
        }
        self.ledger_file.write_text(json.dumps(data, indent=2, default=str))
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
            for s in raw.get("sessions", []):
                try:
                    inst.sessions.append(LedgerEntry(**s))
                except Exception as exc:
                    logger.warning("Skipping malformed ledger entry: %s", exc)
        except Exception as exc:
            logger.error("Could not load ledger from %s: %s", ledger_file, exc)
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

        if trade_records:
            for t in trade_records:
                _accumulate_hour(t, by_hour)
                _accumulate_delta(t, by_delta)
                _accumulate_spread(t, by_spread)
                _accumulate_reject(t, reject_reasons)

        entry = LedgerEntry(
            date=r.date,
            total_trades=wins + losses,
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
        )

        # Replace if same date already present
        self.sessions = [s for s in self.sessions if s.date != entry.date]
        self.sessions.append(entry)
        self.sessions.sort(key=lambda s: s.date)
        return entry

    # ── Cumulative stats ──────────────────────────────────────────────────────

    def compute_cumulative(self) -> Dict[str, Any]:
        if not self.sessions:
            return _empty_cumulative()

        total_wins = sum(s.wins for s in self.sessions)
        total_losses = sum(s.losses for s in self.sessions)
        total_trades = total_wins + total_losses
        total_pnl = sum(s.realized_pnl for s in self.sessions)
        expectancy = (total_pnl / total_trades) if total_trades else 0.0
        win_rate = (total_wins / total_trades) if total_trades else None

        # Profit factor
        wins_sum = sum(
            s.realized_pnl for s in self.sessions
            if s.realized_pnl > 0
        )
        losses_sum = abs(sum(
            s.realized_pnl for s in self.sessions
            if s.realized_pnl < 0
        ))
        profit_factor = (wins_sum / losses_sum) if losses_sum > 0 else None

        # Max drawdown from cumulative PnL curve
        cum_pnl = 0.0
        peak = 0.0
        max_drawdown = 0.0
        for s in sorted(self.sessions, key=lambda x: x.date):
            cum_pnl += s.realized_pnl
            if cum_pnl > peak:
                peak = cum_pnl
            dd = peak - cum_pnl
            if dd > max_drawdown:
                max_drawdown = dd

        # Merge per-dimension dicts
        pnl_by_strategy = _merge_dimension(self.sessions, "by_strategy")
        pnl_by_hour = _merge_dimension(self.sessions, "by_entry_hour")
        pnl_by_delta = _merge_dimension(self.sessions, "by_delta_bucket")
        pnl_by_spread = _merge_dimension(self.sessions, "by_spread_bucket")

        # Reject reasons
        reject_counts: Dict[str, int] = {}
        for s in self.sessions:
            for reason, cnt in s.reject_reasons.items():
                reject_counts[reason] = reject_counts.get(reason, 0) + cnt

        return {
            "trading_days": len(self.sessions),
            "total_trades": total_trades,
            "total_pnl": round(total_pnl, 2),
            "expectancy": round(expectancy, 2),
            "win_rate": round(win_rate, 4) if win_rate is not None else None,
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


def _merge_dimension(sessions: List[LedgerEntry], attr: str) -> Dict[str, Dict]:
    merged: Dict[str, Dict] = {}
    for s in sessions:
        for key, val in getattr(s, attr, {}).items():
            if key not in merged:
                merged[key] = {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0}
            merged[key]["trades"] += val.get("trades", 0)
            merged[key]["wins"] += val.get("wins", 0)
            merged[key]["losses"] += val.get("losses", 0)
            merged[key]["pnl"] = round(merged[key]["pnl"] + val.get("pnl", 0.0), 2)
    return merged


def _delta_bucket(delta: Optional[float]) -> str:
    if delta is None:
        return "unknown"
    if delta < 0.30:
        return "low (<0.30)"
    if delta < 0.40:
        return "mid (0.30-0.40)"
    if delta < 0.50:
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
            from zoneinfo import ZoneInfo
            ts = ts.replace(tzinfo=ZoneInfo("America/New_York"))
        ts = ts.astimezone(ZoneInfo("America/New_York"))
    hour_key = ts.strftime("%H:00")
    if hour_key not in by_hour:
        by_hour[hour_key] = {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0}
    by_hour[hour_key]["trades"] += 1
    pnl_f = float(pnl)
    if pnl_f > 0:
        by_hour[hour_key]["wins"] += 1
    elif pnl_f < 0:
        by_hour[hour_key]["losses"] += 1
    by_hour[hour_key]["pnl"] = round(by_hour[hour_key]["pnl"] + pnl_f, 2)


def _accumulate_delta(trade, by_delta: Dict) -> None:
    delta = getattr(trade, "delta", None)
    pnl = getattr(trade, "realized_pnl", None)
    if pnl is None:
        return
    bucket = _delta_bucket(delta)
    if bucket not in by_delta:
        by_delta[bucket] = {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0}
    by_delta[bucket]["trades"] += 1
    pnl_f = float(pnl)
    if pnl_f > 0:
        by_delta[bucket]["wins"] += 1
    elif pnl_f < 0:
        by_delta[bucket]["losses"] += 1
    by_delta[bucket]["pnl"] = round(by_delta[bucket]["pnl"] + pnl_f, 2)


def _accumulate_spread(trade, by_spread: Dict) -> None:
    spread_pct = getattr(trade, "spread_pct", None)
    pnl = getattr(trade, "realized_pnl", None)
    if pnl is None:
        return
    bucket = _spread_bucket(spread_pct)
    if bucket not in by_spread:
        by_spread[bucket] = {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0}
    by_spread[bucket]["trades"] += 1
    pnl_f = float(pnl)
    if pnl_f > 0:
        by_spread[bucket]["wins"] += 1
    elif pnl_f < 0:
        by_spread[bucket]["losses"] += 1
    by_spread[bucket]["pnl"] = round(by_spread[bucket]["pnl"] + pnl_f, 2)


def _accumulate_reject(trade, reject_reasons: Dict) -> None:
    status = getattr(trade, "status", "")
    if status != "rejected":
        return
    reason = getattr(trade, "rejection_reason", None) or "unknown"
    # Truncate long reasons to a canonical label
    reason_key = reason.split(":")[0].strip()[:64]
    reject_reasons[reason_key] = reject_reasons.get(reason_key, 0) + 1
