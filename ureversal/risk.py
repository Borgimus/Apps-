"""Risk layer (§7 of the spec) — hard, strategy-independent controls.

Every entry passes through `RiskManager.can_enter()`; every close through
`record_exit()`. State (daily counters, consecutive losing days, PDT rolling
day-trade log) persists to a JSON file so restarts don't reset limits.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from .config import Config

log = logging.getLogger("ureversal.risk")


@dataclass
class RiskDecision:
    allowed: bool
    reason: str = "ok"


class RiskManager:
    def __init__(self, cfg: Config, state_path: Path | None = None):
        self.cfg = cfg
        r = cfg.risk
        self.max_positions = int(r["max_positions"])
        self.max_trades_day = int(r["max_trades_per_day"])
        self.daily_loss_limit_pct = float(r["daily_loss_limit_pct"])
        self.max_consec_losing_days = int(r["consecutive_losing_days_halt"])
        self.max_alloc_pct = float(r["max_alloc_pct"])
        self.pdt_min_equity = float(r["pdt_min_equity"])
        self.pdt_max_daytrades = int(r["pdt_max_daytrades_per_5d"])
        self.kill_switch = Path(r["kill_switch_file"])
        self.state_path = state_path or (cfg.cache_dir / "risk_state.json")
        self.state = self._load()

    # ── persistence ──

    def _load(self) -> dict:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text())
        return {
            "day": None,
            "trades_today": 0,
            "pnl_today": 0.0,
            "day_start_equity": None,
            "consecutive_losing_days": 0,
            "halted_until_manual_reset": False,
            "day_trades": [],  # ISO dates of round trips (PDT rolling window)
            "open_positions": 0,
        }

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self.state, indent=1))

    # ── day lifecycle ──

    def start_day(self, day: dt.date, equity: float) -> None:
        s = self.state
        prev_day, prev_pnl = s["day"], s["pnl_today"]
        if prev_day is not None and prev_day != day.isoformat():
            if prev_pnl < 0:
                s["consecutive_losing_days"] += 1
                if s["consecutive_losing_days"] >= self.max_consec_losing_days:
                    s["halted_until_manual_reset"] = True
                    log.critical("circuit breaker: %d consecutive losing days — HALTED",
                                 s["consecutive_losing_days"])
            elif prev_pnl > 0:
                s["consecutive_losing_days"] = 0
        s["day"] = day.isoformat()
        s["trades_today"] = 0
        s["pnl_today"] = 0.0
        s["day_start_equity"] = equity
        s["open_positions"] = 0
        cutoff = (day - dt.timedelta(days=10)).isoformat()
        s["day_trades"] = [d for d in s["day_trades"] if d >= cutoff]
        self._save()

    def manual_reset(self) -> None:
        self.state["halted_until_manual_reset"] = False
        self.state["consecutive_losing_days"] = 0
        self._save()

    # ── checks ──

    def kill_switch_active(self) -> bool:
        return self.kill_switch.exists()

    def can_enter(self, now_et: dt.datetime, equity: float) -> RiskDecision:
        s = self.state
        if self.kill_switch_active():
            return RiskDecision(False, "kill switch active")
        if s["halted_until_manual_reset"]:
            return RiskDecision(False, "consecutive-loss circuit breaker (manual reset required)")
        t = now_et.time()
        if not (self.cfg.entry_start <= t <= self.cfg.last_entry):
            return RiskDecision(False, f"outside entry window ({t})")
        if s["open_positions"] >= self.max_positions:
            return RiskDecision(False, "max positions open")
        if s["trades_today"] >= self.max_trades_day:
            return RiskDecision(False, "max trades/day reached")
        base = s["day_start_equity"] or equity
        if s["pnl_today"] <= -base * self.daily_loss_limit_pct / 100:
            return RiskDecision(False, "daily loss limit hit")
        if equity < self.pdt_min_equity:
            # rolling 5-business-day window ≈ last 7 calendar days
            recent = [d for d in s["day_trades"]
                      if d >= (now_et.date() - dt.timedelta(days=7)).isoformat()]
            if len(recent) >= self.pdt_max_daytrades:
                return RiskDecision(False, "PDT limit (equity < $25k)")
        return RiskDecision(True)

    def position_size(self, equity: float, price: float) -> int:
        return max(int(equity * self.max_alloc_pct / 100 / price), 0)

    # ── recording ──

    def record_entry(self) -> None:
        self.state["open_positions"] += 1
        self.state["trades_today"] += 1
        self._save()

    def record_exit(self, net_pnl: float, day: dt.date) -> None:
        s = self.state
        s["open_positions"] = max(s["open_positions"] - 1, 0)
        s["pnl_today"] += net_pnl
        s["day_trades"].append(day.isoformat())
        self._save()
        base = s["day_start_equity"]
        if base and s["pnl_today"] <= -base * self.daily_loss_limit_pct / 100:
            log.warning("daily loss limit reached (%.2f) — no more entries today",
                        s["pnl_today"])
