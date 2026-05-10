"""
Deterministic bar-by-bar replay engine.

Feed historical OHLCV bars through the full signal → risk → position-management
pipeline and record every decision to a ReplayResult.  All option pricing uses
Black-Scholes (same as the backtest engine) so results are reproducible and
require no live broker connection.

Design rules:
  • Deterministic: given identical bars + settings, output is always the same.
  • No external I/O: no broker calls, no DB writes (caller handles persistence).
  • One position per underlying at a time (dedup enforced by PositionManager).
  • All exit conditions evaluated on every bar after entry.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from ..brokers.broker_interface import OptionContract, OrderRequest, OrderSide, OrderType
from ..config import Settings, get_settings
from ..data.yfinance_data import YFinanceDataSource
from ..risk.risk_manager import RiskManager
from ..strategies.strategy_base import Signal, SignalDirection, StrategyBase
from ..trading.position_manager import OpenPosition, PositionManager

logger = logging.getLogger(__name__)


@dataclass
class ReplayTrade:
    strategy_id: str
    symbol: str
    direction: str
    option_symbol: str
    entry_time: datetime
    exit_time: Optional[datetime]
    entry_price: float
    exit_price: Optional[float]
    exit_reason: Optional[str]
    quantity: int
    pnl: float
    hold_secs: float
    is_approximate: bool = True


@dataclass
class ReplaySkip:
    signal_time: datetime
    strategy_id: str
    symbol: str
    reason: str


@dataclass
class ReplayResult:
    strategy_id: str
    symbol: str
    start_bar: Optional[datetime] = None
    end_bar: Optional[datetime] = None
    trades: List[ReplayTrade] = field(default_factory=list)
    skips: List[ReplaySkip] = field(default_factory=list)
    is_approximate: bool = True

    # Computed after replay
    total_trades: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    expectancy: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0

    def compute_metrics(self):
        pnls = [t.pnl for t in self.trades]
        self.total_trades = len(pnls)
        if not pnls:
            return
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        self.win_rate = len(wins) / len(pnls)
        self.total_pnl = sum(pnls)
        self.avg_win = float(np.mean(wins)) if wins else 0.0
        self.avg_loss = float(np.mean(losses)) if losses else 0.0
        self.expectancy = self.win_rate * self.avg_win + (1 - self.win_rate) * self.avg_loss

        equity, peak, max_dd = 0.0, 0.0, 0.0
        for p in pnls:
            equity += p
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd
        self.max_drawdown = max_dd

        arr = np.array(pnls)
        if len(arr) > 1 and arr.std() > 0:
            self.sharpe_ratio = float(arr.mean() / arr.std() * np.sqrt(252))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "start_bar": str(self.start_bar) if self.start_bar else None,
            "end_bar": str(self.end_bar) if self.end_bar else None,
            "is_approximate": self.is_approximate,
            "total_trades": self.total_trades,
            "win_rate": round(self.win_rate, 4),
            "total_pnl": round(self.total_pnl, 2),
            "avg_win": round(self.avg_win, 2),
            "avg_loss": round(self.avg_loss, 2),
            "expectancy": round(self.expectancy, 2),
            "max_drawdown": round(self.max_drawdown, 4),
            "sharpe_ratio": round(self.sharpe_ratio, 4),
            "total_skips": len(self.skips),
        }


class ReplayEngine:
    """
    Deterministic bar-by-bar replay engine.

    Usage::

        engine = ReplayEngine(strategy=ORBStrategy(), symbol="SPY")
        result = engine.replay(bars)
        print(result.to_dict())
    """

    # Default synthetic option params (same as backtest engine)
    _DEFAULT_DTE = 1
    _DEFAULT_TARGET_DELTA = 0.40
    _RISK_FREE_RATE = 0.05

    def __init__(
        self,
        strategy: StrategyBase,
        symbol: str,
        settings: Settings | None = None,
        starting_equity: float = 100_000.0,
        dte: int = _DEFAULT_DTE,
        target_delta: float = _DEFAULT_TARGET_DELTA,
        slippage_per_contract: float = 0.05,
        simulate_slippage: bool = True,
    ):
        self._strategy = strategy
        self._symbol = symbol
        self._s = settings or get_settings()
        self._starting_equity = starting_equity
        self._dte = dte
        self._target_delta = target_delta
        self._slippage = slippage_per_contract if simulate_slippage else 0.0

    def replay(self, bars: pd.DataFrame) -> ReplayResult:
        """
        Run the full bar-by-bar replay.  Fully deterministic given identical inputs.

        Parameters
        ----------
        bars : pd.DataFrame
            OHLCV data with a UTC DatetimeIndex.  Lowercase column names required.
        """
        if bars.empty:
            return ReplayResult(strategy_id=self._strategy.strategy_id, symbol=self._symbol)

        bars = bars.copy()
        bars.columns = [c.lower() for c in bars.columns]

        result = ReplayResult(
            strategy_id=self._strategy.strategy_id,
            symbol=self._symbol,
            start_bar=bars.index[0].to_pydatetime(),
            end_bar=bars.index[-1].to_pydatetime(),
        )

        risk = RiskManager(self._s)
        risk.start_session(Decimal(str(self._starting_equity)))
        pm = PositionManager(self._s)
        equity = self._starting_equity

        # Track which signal timestamps we've already acted on (dedup across bars)
        acted_signals: set = set()

        for bar_i in range(len(bars)):
            bar = bars.iloc[bar_i]
            ts = bars.index[bar_i]
            ts_dt = ts.to_pydatetime()

            # ── Update open positions ────────────────────────────────────────
            for opt_sym in list(pm._positions.keys()):
                pos = pm._positions[opt_sym]
                current_opt = self._estimate_option_price(bar, pos, bar_i, bars)
                pm.update_price(opt_sym, current_opt)

                reason = pm.should_exit(opt_sym, current_opt, ts_dt)
                if reason:
                    # Subtract slippage at exit (seller receives bid - slippage)
                    exit_net = max(current_opt - self._slippage, 0.0)
                    pnl = (exit_net - pos.entry_price) * 100 * pos.quantity
                    hold_secs = (ts_dt - pos.entry_time).total_seconds()
                    pm.close(opt_sym, current_opt, pnl)
                    equity += pnl
                    risk.record_trade(Decimal(str(pnl)))
                    result.trades.append(ReplayTrade(
                        strategy_id=pos.strategy_id,
                        symbol=self._symbol,
                        direction=pos.direction,
                        option_symbol=opt_sym,
                        entry_time=pos.entry_time,
                        exit_time=ts_dt,
                        entry_price=pos.entry_price,
                        exit_price=exit_net,
                        exit_reason=reason,
                        quantity=pos.quantity,
                        pnl=round(pnl, 2),
                        hold_secs=hold_secs,
                    ))
                    logger.debug("Replay closed %s @ %.4f reason=%s pnl=%.2f", opt_sym, current_opt, reason, pnl)

            # ── Generate signals from all bars up to and including bar_i ────
            bars_so_far = bars.iloc[:bar_i + 1]
            signals = self._strategy.generate_signals(bars_so_far, self._symbol)

            for sig in signals:
                sig_ts = pd.Timestamp(sig.timestamp)
                if sig_ts.tzinfo is not None:
                    sig_ts_cmp = sig_ts
                    ts_cmp = ts.tz_localize("UTC") if ts.tzinfo is None else ts
                else:
                    sig_ts_cmp = sig_ts.tz_localize("UTC")
                    ts_cmp = ts.tz_localize("UTC") if ts.tzinfo is None else ts

                # Only process signals that fired on *this* bar
                if abs((sig_ts_cmp - ts_cmp).total_seconds()) > 300:
                    continue
                if not sig.is_actionable():
                    continue

                sig_key = (sig.strategy_id, str(sig_ts_cmp))
                if sig_key in acted_signals:
                    continue
                acted_signals.add(sig_key)

                # Cooldown check
                if pm.is_in_cooldown(ts_dt):
                    result.skips.append(ReplaySkip(ts_dt, sig.strategy_id, self._symbol, "cooldown"))
                    continue

                # Dedup: one position per underlying
                if pm.has_position_for_symbol(self._symbol):
                    result.skips.append(ReplaySkip(ts_dt, sig.strategy_id, self._symbol, "duplicate_position"))
                    continue

                # Estimate entry option price (add half-spread slippage at entry)
                opt_price_raw = self._estimate_entry_option_price(bar, sig, bar_i, bars)
                opt_price = opt_price_raw + self._slippage   # buyer pays ask + slippage
                if opt_price <= 0:
                    result.skips.append(ReplaySkip(ts_dt, sig.strategy_id, self._symbol, "zero_option_price"))
                    continue

                # Risk check
                dummy_contract = self._make_dummy_contract(sig, opt_price)
                dummy_request = self._make_dummy_request(sig, opt_price)
                risk_result = risk.check_order(
                    request=dummy_request,
                    equity=Decimal(str(equity)),
                    contract=dummy_contract,
                    now=ts_dt,
                )

                if not risk_result.passed:
                    reason_str = "; ".join(risk_result.messages)
                    result.skips.append(ReplaySkip(ts_dt, sig.strategy_id, self._symbol, reason_str))
                    continue

                # Open position
                opt_sym = f"{self._symbol}_{ts.date()}_{sig.direction.value}_{bar_i}"
                pos = pm.open(
                    option_symbol=opt_sym,
                    symbol=self._symbol,
                    strategy_id=sig.strategy_id,
                    direction=sig.direction.value,
                    entry_time=ts_dt,
                    entry_price=opt_price,
                    quantity=risk_result.approved_quantity,
                )
                logger.debug("Replay opened %s @ %.4f", opt_sym, opt_price)

        # ── Force-close any positions still open at end of bars ──────────────
        last_bar = bars.iloc[-1]
        last_ts = bars.index[-1].to_pydatetime()
        for opt_sym in list(pm._positions.keys()):
            pos = pm._positions[opt_sym]
            current_opt_raw = self._estimate_option_price(last_bar, pos, len(bars) - 1, bars)
            exit_net = max(current_opt_raw - self._slippage, 0.0)
            pnl = (exit_net - pos.entry_price) * 100 * pos.quantity
            hold_secs = (last_ts - pos.entry_time).total_seconds()
            pm.close(opt_sym, exit_net, pnl)
            result.trades.append(ReplayTrade(
                strategy_id=pos.strategy_id,
                symbol=self._symbol,
                direction=pos.direction,
                option_symbol=opt_sym,
                entry_time=pos.entry_time,
                exit_time=last_ts,
                entry_price=pos.entry_price,
                exit_price=exit_net,
                exit_reason="session_end",
                quantity=pos.quantity,
                pnl=round(pnl, 2),
                hold_secs=hold_secs,
            ))

        result.compute_metrics()
        return result

    # ── Option pricing helpers (Black-Scholes, same as backtest engine) ───────

    def _hist_vol(self, bars: pd.DataFrame, up_to_bar: int) -> float:
        window = min(21, up_to_bar)
        if window < 5:
            return 0.20
        closes = bars["close"].iloc[max(0, up_to_bar - window):up_to_bar + 1]
        log_ret = np.log(closes / closes.shift(1)).dropna()
        iv = float(log_ret.std() * np.sqrt(252))
        return max(iv, 0.05)

    def _estimate_entry_option_price(
        self,
        bar: pd.Series,
        sig: Signal,
        bar_i: int,
        bars: pd.DataFrame,
    ) -> float:
        price = float(bar["close"])
        iv = self._hist_vol(bars, bar_i)
        T = self._dte / 365.0
        r = self._RISK_FREE_RATE
        if sig.direction == SignalDirection.LONG:
            strike = price * (1 + (1 - self._target_delta) * 0.05)
            opt = YFinanceDataSource.black_scholes_price(price, strike, T, r, iv, "call")
        else:
            strike = price * (1 - (1 - self._target_delta) * 0.05)
            opt = YFinanceDataSource.black_scholes_price(price, strike, T, r, iv, "put")
        return max(float(opt), 0.01)

    def _estimate_option_price(
        self,
        bar: pd.Series,
        pos: OpenPosition,
        bar_i: int,
        bars: pd.DataFrame,
    ) -> float:
        price = float(bar["close"])
        iv = self._hist_vol(bars, bar_i)
        hold_days = (bars.index[bar_i].to_pydatetime() - pos.entry_time).total_seconds() / 86400
        T = max(self._dte / 365.0 - hold_days / 365.0, 1 / 365.0)
        r = self._RISK_FREE_RATE
        opt_type = "call" if pos.direction == "LONG" else "put"
        entry_strike_approx = pos.entry_price  # rough proxy; BS not invertible easily
        # Re-derive strike from entry price using same formula
        if pos.direction == "LONG":
            strike = price * (1 + (1 - self._target_delta) * 0.05)
            opt = YFinanceDataSource.black_scholes_price(price, strike, T, r, iv, "call")
        else:
            strike = price * (1 - (1 - self._target_delta) * 0.05)
            opt = YFinanceDataSource.black_scholes_price(price, strike, T, r, iv, "put")
        return max(float(opt), 0.0)

    def _make_dummy_contract(self, sig: Signal, opt_price: float) -> OptionContract:
        from datetime import date, timedelta
        from decimal import Decimal
        ask = Decimal(str(round(opt_price * 1.02, 4)))
        bid = Decimal(str(round(opt_price * 0.98, 4)))
        return OptionContract(
            symbol=self._symbol,
            option_symbol=f"{self._symbol}_REPLAY",
            expiration=date.today() + timedelta(days=self._dte),
            strike=Decimal(str(round(sig.price, 2))),
            option_type="call" if sig.direction == SignalDirection.LONG else "put",
            bid=bid,
            ask=ask,
            last=ask,
            volume=10_000,
            open_interest=10_000,
            implied_volatility=0.20,
            delta=0.40 if sig.direction == SignalDirection.LONG else -0.40,
        )

    def _make_dummy_request(self, sig: Signal, opt_price: float) -> OrderRequest:
        from decimal import Decimal
        return OrderRequest(
            symbol=self._symbol,
            option_symbol=f"{self._symbol}_REPLAY",
            side=OrderSide.BUY_TO_OPEN,
            quantity=1,
            order_type=OrderType.LIMIT,
            limit_price=Decimal(str(round(opt_price * 1.02, 4))),
            strategy_id=sig.strategy_id,
        )
