"""Live trading orchestrator (PAPER ONLY).

`tick()` runs one decision cycle:
  1. fetch/reconcile broker truth (broker wins; a mismatch blocks new risk),
  2. manage every open position (5R partial + breakeven, daily-close exit, missing-stop alert),
  3. if the fail-closed gate allows, evaluate qualified candidates for entry.

Mode gating is strict: only **PAPER_AUTO** submits orders. SHADOW and PAPER_CONFIRM compute the
exact same decisions but emit *proposals* and never touch the broker (PAPER_CONFIRM proposals await
operator approval out-of-band). All broker access goes through the injected paper adapter with
retry, so a retry reuses the same idempotent client_order_id and cannot duplicate a position.
Managing/closing existing positions is never blocked — only new entries are gated.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.data.calendar import is_regular_session
from src.execution.orders import build_final_exit, build_partial_exit, build_protective_stop
from src.live.entry import CandidateSetup, evaluate_entry
from src.live.manage import (
    FinalExitIntent,
    LivePosition,
    MissingStopIntent,
    PartialIntent,
    manage_open_position,
)
from src.notifications.interface import Event, Notification, Severity
from src.risk.portfolio import OpenRisk
from src.runtime.controls import EntryContext, can_open_new_entry


@dataclass
class GateFlags:
    paper_verified: bool = True
    data_ok: bool = True
    batch_fresh: bool = True
    db_ok: bool = True
    clock_ok: bool = True
    emergency_stop: bool = False


@dataclass
class MarketView:
    snapshot: object                    # MarketSnapshot
    last_price: float
    session_completed: bool = False
    daily_close: float | None = None
    sma10_at_close: float | None = None
    high_since_entry: float | None = None


@dataclass
class LoopDeps:
    broker: object                      # BrokerInterface (paper adapter)
    reconcile: object                   # () -> ReconResult
    get_positions: object               # () -> list[LivePosition]
    get_candidates: object              # () -> list[CandidateSetup]
    get_market: object                  # (symbol) -> MarketView
    notifier: object                    # Notifier
    config: dict
    mode: str
    now: object                         # tz-aware datetime
    flags: GateFlags = field(default_factory=GateFlags)
    retry: object | None = None
    sleep: object | None = None


@dataclass
class TickReport:
    mode: str
    managed: list = field(default_factory=list)          # (symbol, [intent kinds])
    submitted: list[str] = field(default_factory=list)    # client_order_ids submitted
    proposals: list[dict] = field(default_factory=list)   # shadow / confirm proposals
    entry_rejections: list = field(default_factory=list)  # (symbol, reason)
    blockers: list[str] = field(default_factory=list)
    risk_blocked: list[str] = field(default_factory=list)


class LiveOrchestrator:
    def __init__(self, deps: LoopDeps):
        self.d = deps

    # -- helpers -------------------------------------------------------------
    def _acts(self) -> bool:
        return self.d.mode == "PAPER_AUTO"

    def _notify(self, event: Event, severity: Severity, title: str, body: str, data: dict) -> None:
        self.d.notifier.send(Notification(event, severity, title, body, data=data))

    def _submit(self, order) -> str:
        self.d.broker.submit_order(order, retry=self.d.retry, sleep=self.d.sleep)
        return order.client_order_id

    # -- one cycle -----------------------------------------------------------
    def tick(self) -> TickReport:
        rpt = TickReport(mode=self.d.mode)
        cfg = self.d.config

        account = self.d.broker.get_account()   # verifies paper endpoint / not blocked
        recon = self.d.reconcile()
        if not recon.ok:
            rpt.blockers.extend(f"recon:{i.kind.value}:{i.symbol}" for i in recon.incidents)

        positions = list(self.d.get_positions())
        open_symbols = {p.symbol for p in positions}
        committed_risk = sum(
            max(0.0, p.entry_price - p.initial_stop) * p.open_shares
            for p in positions if p.has_stop
        )

        # 2) manage open positions (never blocked by the entry gate)
        for pos in positions:
            mv: MarketView = self.d.get_market(pos.symbol)
            res = manage_open_position(
                pos, last_price=mv.last_price, session_completed=mv.session_completed,
                daily_close=mv.daily_close, sma10_at_close=mv.sma10_at_close,
                config=cfg, high_since_entry=mv.high_since_entry,
            )
            rpt.managed.append((pos.symbol, [i.kind for i in res.intents]))
            for intent in res.intents:
                self._handle_manage_intent(pos, intent, rpt)

        # 3) entries — fail-closed gate first
        gate = self._build_gate(account, recon, positions)
        decision = can_open_new_entry(gate)
        if not decision.allowed:
            rpt.blockers.extend(decision.blockers)
            return rpt

        open_risk = [OpenRisk(p.symbol, p.open_shares, p.entry_price, p.initial_stop)
                     for p in positions]
        for setup in self.d.get_candidates():
            if setup.symbol in open_symbols:
                rpt.entry_rejections.append((setup.symbol, "duplicate_position_or_pending"))
                continue
            mv = self.d.get_market(setup.symbol)
            ed = evaluate_entry(
                setup, snapshot=mv.snapshot, last_price=mv.last_price, account=account,
                open_positions=open_risk, open_symbols=open_symbols,
                committed_risk=committed_risk, config=cfg, now=self.d.now,
            )
            if not ed.accepted:
                rpt.entry_rejections.append((setup.symbol, ed.reject_reason))
                continue
            self._handle_entry(ed, rpt)
            open_symbols.add(setup.symbol)   # no duplicate entries within one tick

        return rpt

    # -- intent handling -----------------------------------------------------
    def _handle_manage_intent(self, pos: LivePosition, intent, rpt: TickReport) -> None:
        if isinstance(intent, MissingStopIntent):
            rpt.risk_blocked.append(pos.symbol)
            self._notify(Event.STOP_MISSING, Severity.CRITICAL, "protective stop missing",
                         intent.reason, {"symbol": pos.symbol, "trade_id": pos.trade_id})
            return

        if isinstance(intent, PartialIntent):
            if self._acts():
                partial = build_partial_exit(trade_id=pos.trade_id, symbol=pos.symbol,
                                             qty=intent.sell_shares, limit_price=intent.limit_price)
                rpt.submitted.append(self._submit(partial))
                self._notify(Event.PARTIAL_5R_SUBMITTED, Severity.INFO, "5R partial submitted",
                             f"{intent.sell_shares} sh", {"symbol": pos.symbol})
                if intent.new_stop_price is not None:
                    new_stop = build_protective_stop(
                        trade_id=pos.trade_id, symbol=pos.symbol,
                        qty=pos.open_shares - intent.sell_shares, stop_price=intent.new_stop_price)
                    rpt.submitted.append(self._submit(new_stop))
                    self._notify(Event.STOP_MOVED_BREAKEVEN, Severity.INFO, "stop -> breakeven",
                                 f"{intent.new_stop_price}", {"symbol": pos.symbol})
            else:
                rpt.proposals.append({"kind": "PARTIAL", "symbol": pos.symbol,
                                      "sell_shares": intent.sell_shares, "mode": self.d.mode})
            return

        if isinstance(intent, FinalExitIntent):
            if self._acts():
                fx = build_final_exit(trade_id=pos.trade_id, symbol=pos.symbol,
                                      qty=intent.sell_shares, session_date=str(self.d.now.date()))
                rpt.submitted.append(self._submit(fx))
                self._notify(Event.DAILY_CLOSE_EXIT_TRIGGERED, Severity.INFO, "daily-close exit",
                             f"{intent.sell_shares} sh", {"symbol": pos.symbol})
            else:
                rpt.proposals.append({"kind": "FINAL_EXIT", "symbol": pos.symbol,
                                      "sell_shares": intent.sell_shares, "mode": self.d.mode})

    def _handle_entry(self, ed, rpt: TickReport) -> None:
        if self._acts():
            rpt.submitted.append(self._submit(ed.entry_order))
            rpt.submitted.append(self._submit(ed.stop_order))   # attached protective stop
            self._notify(Event.ENTRY_SUBMITTED, Severity.INFO, "entry submitted",
                         f"{ed.shares} sh {ed.entry_order.symbol}",
                         {"symbol": ed.entry_order.symbol, "trade_id": ed.trade_id})
        else:
            rpt.proposals.append({"kind": "ENTRY", "symbol": ed.entry_order.symbol,
                                  "shares": ed.shares, "mode": self.d.mode})

    # -- gate ----------------------------------------------------------------
    def _build_gate(self, account, recon, positions) -> EntryContext:
        cfg_risk = self.d.config["risk"]
        all_stops = all(p.has_stop for p in positions) and not any(
            i.kind.value == "MISSING_STOP" for i in recon.incidents)
        risk_ok = len(positions) < int(cfg_risk.get("max_concurrent_positions", 5))
        return EntryContext(
            paper_endpoint_verified=self.d.flags.paper_verified,
            data_fresh=self.d.flags.data_ok,
            broker_reconciled=recon.ok,
            tc2000_batch_fresh=self.d.flags.batch_fresh,
            db_ok=self.d.flags.db_ok,
            all_positions_have_stops=all_stops,
            risk_limit_ok=risk_ok,
            buying_power_ok=account.buying_power > 0,
            regular_session=is_regular_session(self.d.now),
            clock_ok=self.d.flags.clock_ok,
            no_duplicate_intent=True,
            emergency_stop=self.d.flags.emergency_stop,
        )
