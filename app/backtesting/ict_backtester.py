"""
ICT-specific backtester.

Runs the ICT strategy over historical 1-minute OHLCV data in a rolling
forward-walk simulation.  Trades are simulated with realistic fill logic:
  • Entry fills at the entry_price on the first bar where price touches
    or crosses the FVG boundary.
  • SL fills when the bar's low (long) or high (short) crosses the stop.
  • TP fills when the bar's high (long) or low (short) crosses the target.
  • Bar order: check SL before TP to be conservative.

Performance metrics computed
─────────────────────────────
  win_rate, avg_rr, profit_factor, expectancy, max_drawdown,
  total_return, monthly_return, sharpe_ratio,
  long_win_rate, short_win_rate, total_trades
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Generator, Iterator, List, Optional

import numpy as np
import pandas as pd

from ..strategies.ict import ICTConfig, ICTSignal, ICTStrategy
from ..strategies.strategy_base import SignalDirection

logger = logging.getLogger(__name__)


# ── Trade record ──────────────────────────────────────────────────────────────

@dataclass
class ICTTradeRecord:
    symbol: str
    direction: str                  # "long" | "short"
    entry_time: datetime
    exit_time: Optional[datetime]
    entry_price: float
    exit_price: Optional[float]
    stop_loss: float
    take_profit: float
    position_size: float
    risk_amount: float
    pnl: float = 0.0               # raw dollar PnL
    rr_achieved: float = 0.0       # actual R:R multiple realised
    trade_duration_minutes: int = 0
    exit_reason: str = ""           # "sl", "tp", "eod", "signal"
    fvg_type: str = ""
    sweep_type: str = ""

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "entry_time": self.entry_time.isoformat() if self.entry_time else None,
            "exit_time": self.exit_time.isoformat() if self.exit_time else None,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "position_size": self.position_size,
            "risk_amount": round(self.risk_amount, 2),
            "pnl": round(self.pnl, 2),
            "rr_achieved": round(self.rr_achieved, 3),
            "trade_duration_minutes": self.trade_duration_minutes,
            "exit_reason": self.exit_reason,
            "fvg_type": self.fvg_type,
            "sweep_type": self.sweep_type,
        }


# ── Backtest result ───────────────────────────────────────────────────────────

@dataclass
class ICTBacktestResult:
    symbol: str
    start_date: str
    end_date: str
    strategy_params: Dict[str, Any]
    trades: List[ICTTradeRecord] = field(default_factory=list)

    # Aggregate metrics (populated by compute_metrics)
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    long_win_rate: float = 0.0
    short_win_rate: float = 0.0
    avg_rr: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    total_pnl: float = 0.0
    total_return: float = 0.0
    monthly_return: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    equity_curve: List[float] = field(default_factory=list)
    monthly_pnl: Dict[str, float] = field(default_factory=dict)

    def compute_metrics(self, starting_equity: float = 100_000.0) -> None:
        self.total_trades = len(self.trades)
        if self.total_trades == 0:
            return

        pnls = [t.pnl for t in self.trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        self.winning_trades = len(wins)
        self.losing_trades = len(losses)
        self.win_rate = self.winning_trades / self.total_trades

        # Directional win rates
        longs = [t for t in self.trades if t.direction == "long"]
        shorts = [t for t in self.trades if t.direction == "short"]
        self.long_win_rate = (
            len([t for t in longs if t.pnl > 0]) / len(longs) if longs else 0.0
        )
        self.short_win_rate = (
            len([t for t in shorts if t.pnl > 0]) / len(shorts) if shorts else 0.0
        )

        self.avg_rr = float(np.mean([t.rr_achieved for t in self.trades]))
        self.total_pnl = float(sum(pnls))
        avg_win = float(np.mean(wins)) if wins else 0.0
        avg_loss = float(np.mean(losses)) if losses else 0.0
        self.expectancy = self.win_rate * avg_win + (1 - self.win_rate) * avg_loss

        gross_wins = sum(wins)
        gross_losses = abs(sum(losses)) if losses else 0.0
        self.profit_factor = gross_wins / gross_losses if gross_losses > 0 else float("inf")

        # Equity curve
        equity = starting_equity
        curve = [equity]
        for pnl in pnls:
            equity += pnl
            curve.append(equity)
        self.equity_curve = curve

        # Total / monthly returns
        self.total_return = (curve[-1] - curve[0]) / curve[0]
        # Approximate monthly return from total return
        if self.trades:
            t_start = self.trades[0].entry_time
            t_end = self.trades[-1].exit_time or self.trades[-1].entry_time
            if t_start and t_end:
                days = max(1, (t_end - t_start).days)
                months = days / 30.44
                self.monthly_return = (
                    (1 + self.total_return) ** (1 / max(months, 1)) - 1
                )

        # Max drawdown
        peak = curve[0]
        max_dd = 0.0
        for val in curve:
            peak = max(peak, val)
            dd = (peak - val) / peak if peak > 0 else 0.0
            max_dd = max(max_dd, dd)
        self.max_drawdown = max_dd

        # Sharpe (daily pnl; annualised by sqrt(252))
        if len(pnls) > 1:
            pnl_arr = np.array(pnls) / starting_equity
            std = float(np.std(pnl_arr))
            if std > 0:
                self.sharpe_ratio = float(np.mean(pnl_arr) / std * np.sqrt(252))

        # Monthly PnL breakdown
        monthly: Dict[str, float] = {}
        for t in self.trades:
            if t.exit_time:
                key = t.exit_time.strftime("%Y-%m")
                monthly[key] = monthly.get(key, 0.0) + t.pnl
        self.monthly_pnl = dict(sorted(monthly.items()))

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "strategy_params": self.strategy_params,
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": round(self.win_rate, 4),
            "long_win_rate": round(self.long_win_rate, 4),
            "short_win_rate": round(self.short_win_rate, 4),
            "avg_rr": round(self.avg_rr, 3),
            "profit_factor": round(self.profit_factor, 4),
            "expectancy": round(self.expectancy, 2),
            "total_pnl": round(self.total_pnl, 2),
            "total_return": round(self.total_return, 4),
            "monthly_return": round(self.monthly_return, 4),
            "max_drawdown": round(self.max_drawdown, 4),
            "sharpe_ratio": round(self.sharpe_ratio, 4),
            "monthly_pnl": {k: round(v, 2) for k, v in self.monthly_pnl.items()},
        }

    def summary(self) -> str:
        d = self.to_dict()
        lines = [
            f"\n{'='*62}",
            f"ICT Backtest | {self.symbol} | {self.start_date} → {self.end_date}",
            f"{'─'*62}",
            f"Trades      : {d['total_trades']} (W:{d['winning_trades']} L:{d['losing_trades']})",
            f"Win Rate    : {d['win_rate']:.1%}  (L:{d['long_win_rate']:.1%} / S:{d['short_win_rate']:.1%})",
            f"Avg R:R     : {d['avg_rr']:.2f}",
            f"Profit Factor: {d['profit_factor']:.2f}",
            f"Expectancy  : ${d['expectancy']:,.2f}",
            f"Total P&L   : ${d['total_pnl']:,.2f}",
            f"Total Return: {d['total_return']:.2%}",
            f"Monthly Ret : {d['monthly_return']:.2%}",
            f"Max Drawdown: {d['max_drawdown']:.2%}",
            f"Sharpe Ratio: {d['sharpe_ratio']:.2f}",
            f"{'='*62}\n",
        ]
        return "\n".join(lines)


# ── Backtester ────────────────────────────────────────────────────────────────

class ICTBacktester:
    """
    Rolling forward-walk backtester for the ICT strategy.

    Parameters
    ----------
    params : dict | None
        ICTStrategy params (override ICTConfig defaults).
    starting_equity : float
    commission_per_unit : float
        Commission in dollars per unit traded (applied on entry + exit).
    slippage_ticks : float
        Slippage in ticks applied to every fill.
    """

    def __init__(
        self,
        params: Dict[str, Any] | None = None,
        starting_equity: float = 100_000.0,
        commission_per_unit: float = 0.0,
        slippage_ticks: float = 0.0,
    ):
        self._params = params or {}
        self._equity = starting_equity
        self._commission = commission_per_unit
        self._slippage_ticks = slippage_ticks
        self._strategy = ICTStrategy(params)
        cfg = self._strategy._ict_cfg
        self._slippage_price = slippage_ticks * cfg.tick_size

    # ── Public API ────────────────────────────────────────────────────────────

    def run(
        self,
        bars: pd.DataFrame,
        symbol: str,
        start: str | None = None,
        end: str | None = None,
    ) -> ICTBacktestResult:
        """
        Run a full backtest.

        Parameters
        ----------
        bars : pd.DataFrame  1-minute OHLCV, UTC DatetimeIndex.
        symbol : str
        start / end : optional date range strings to clip bars.

        Returns
        -------
        ICTBacktestResult with metrics computed.
        """
        bars = self._normalise(bars)

        if start:
            bars = bars[bars.index >= pd.Timestamp(start, tz="UTC")]
        if end:
            bars = bars[bars.index <= pd.Timestamp(end, tz="UTC")]

        if bars.empty:
            logger.warning("No bars in range for %s", symbol)
            return ICTBacktestResult(
                symbol=symbol,
                start_date=start or "",
                end_date=end or "",
                strategy_params=self._params,
            )

        start_date = str(bars.index[0].date())
        end_date = str(bars.index[-1].date())

        # Generate signals over full bar set
        signals: List[ICTSignal] = self._strategy.generate_signals(bars, symbol)
        logger.info(
            "ICTBacktester: %d signals for %s (%s → %s)",
            len(signals),
            symbol,
            start_date,
            end_date,
        )

        # Simulate fills
        trades = self._simulate_trades(bars, signals)

        result = ICTBacktestResult(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            strategy_params=self._params,
            trades=trades,
        )
        result.compute_metrics(self._equity)
        return result

    def trade_replay(
        self, bars: pd.DataFrame, symbol: str
    ) -> Generator[Dict[str, Any], None, None]:
        """
        Yield replay snapshots for frontend visualisation.

        Each snapshot contains the current bar, active trade state, and
        the trade record if a trade just closed.
        """
        bars = self._normalise(bars)
        signals: List[ICTSignal] = self._strategy.generate_signals(bars, symbol)
        sig_by_ts = {s.timestamp: s for s in signals}

        active_trade: Optional[Dict] = None
        equity = self._equity

        for i, (ts, row) in enumerate(bars.iterrows()):
            ts_py = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
            snapshot: Dict[str, Any] = {
                "bar_index": i,
                "timestamp": ts_py.isoformat(),
                "ohlcv": {
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row.get("volume", 0)),
                },
                "equity": equity,
                "active_trade": None,
                "closed_trade": None,
                "signal": None,
            }

            # Check for new signal at this bar
            sig = sig_by_ts.get(ts_py)
            if sig and active_trade is None:
                snapshot["signal"] = sig.to_dict()
                active_trade = {
                    "entry_price": sig.entry_price,
                    "stop_loss": sig.stop_loss,
                    "take_profit": sig.take_profit,
                    "direction": sig.direction.value,
                    "position_size": sig.position_size,
                    "risk_amount": sig.risk_amount,
                    "entry_time": ts_py.isoformat(),
                }

            if active_trade:
                snapshot["active_trade"] = active_trade
                closed = self._check_exit(row, active_trade, i, bars)
                if closed:
                    pnl = closed["pnl"]
                    equity += pnl
                    snapshot["equity"] = equity
                    snapshot["closed_trade"] = closed
                    snapshot["active_trade"] = None
                    active_trade = None

            yield snapshot

    # ── Trade simulation ──────────────────────────────────────────────────────

    def _simulate_trades(
        self, bars: pd.DataFrame, signals: List[ICTSignal]
    ) -> List[ICTTradeRecord]:
        trades: List[ICTTradeRecord] = []
        active: Optional[Dict[str, Any]] = None

        bars_list = list(bars.iterrows())

        for sig in signals:
            if active is not None:
                # Only one trade at a time
                continue

            # Find entry bar (first bar at or after signal timestamp where
            # price trades into the FVG)
            entry_ts = sig.timestamp if hasattr(sig.timestamp, "tzinfo") else pd.Timestamp(sig.timestamp, tz="UTC")

            # Scan forward for entry fill
            entry_idx = None
            entry_price_actual = sig.entry_price
            for i, (ts, row) in enumerate(bars_list):
                if ts < entry_ts:
                    continue
                # Check if bar touches the entry level
                if sig.direction == SignalDirection.LONG:
                    if row["low"] <= sig.entry_price <= row["high"]:
                        entry_idx = i
                        entry_price_actual = sig.entry_price + self._slippage_price
                        break
                else:
                    if row["low"] <= sig.entry_price <= row["high"]:
                        entry_idx = i
                        entry_price_actual = sig.entry_price - self._slippage_price
                        break

            if entry_idx is None:
                continue

            # Scan for exit from entry_idx onward
            trade = self._scan_for_exit(
                bars_list=bars_list,
                start_idx=entry_idx,
                sig=sig,
                entry_price=entry_price_actual,
            )
            if trade:
                trades.append(trade)

        return trades

    def _scan_for_exit(
        self,
        bars_list: list,
        start_idx: int,
        sig: ICTSignal,
        entry_price: float,
    ) -> Optional[ICTTradeRecord]:
        """Scan bars from start_idx until SL, TP, or end of data."""
        is_long = sig.direction == SignalDirection.LONG
        sl = sig.stop_loss
        tp = sig.take_profit
        risk_per_unit = abs(entry_price - sl)

        entry_ts = bars_list[start_idx][0]
        entry_ts_py = entry_ts.to_pydatetime() if hasattr(entry_ts, "to_pydatetime") else entry_ts

        for i in range(start_idx + 1, len(bars_list)):
            ts, row = bars_list[i]
            ts_py = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
            lo = float(row["low"])
            hi = float(row["high"])

            exit_price: Optional[float] = None
            exit_reason = ""

            if is_long:
                # Check SL first (conservative)
                if lo <= sl:
                    exit_price = sl - self._slippage_price
                    exit_reason = "sl"
                elif hi >= tp:
                    exit_price = tp - self._slippage_price
                    exit_reason = "tp"
            else:
                if hi >= sl:
                    exit_price = sl + self._slippage_price
                    exit_reason = "sl"
                elif lo <= tp:
                    exit_price = tp + self._slippage_price
                    exit_reason = "tp"

            if exit_price is not None:
                if is_long:
                    raw_pnl = (exit_price - entry_price) * sig.position_size
                else:
                    raw_pnl = (entry_price - exit_price) * sig.position_size
                commission = self._commission * sig.position_size * 2
                net_pnl = raw_pnl - commission
                rr = (exit_price - entry_price) / risk_per_unit if is_long and risk_per_unit else (entry_price - exit_price) / risk_per_unit if risk_per_unit else 0.0
                duration = int((ts_py - entry_ts_py).total_seconds() / 60)

                return ICTTradeRecord(
                    symbol=sig.symbol,
                    direction=sig.direction.value,
                    entry_time=entry_ts_py,
                    exit_time=ts_py,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    stop_loss=sl,
                    take_profit=tp,
                    position_size=sig.position_size,
                    risk_amount=sig.risk_amount,
                    pnl=round(net_pnl, 2),
                    rr_achieved=round(rr, 3),
                    trade_duration_minutes=duration,
                    exit_reason=exit_reason,
                    fvg_type=sig.fvg.type.value if sig.fvg else "",
                    sweep_type=sig.sweep_event.level_type.value if sig.sweep_event else "",
                )

        # End of data: exit at last bar
        if len(bars_list) > start_idx:
            ts, row = bars_list[-1]
            ts_py = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
            exit_price = float(row["close"])
            if is_long:
                raw_pnl = (exit_price - entry_price) * sig.position_size
            else:
                raw_pnl = (entry_price - exit_price) * sig.position_size
            commission = self._commission * sig.position_size * 2
            net_pnl = raw_pnl - commission
            rr = (exit_price - entry_price) / risk_per_unit if is_long and risk_per_unit else (entry_price - exit_price) / risk_per_unit if risk_per_unit else 0.0
            duration = int(
                (ts_py - entry_ts_py).total_seconds() / 60
            )
            return ICTTradeRecord(
                symbol=sig.symbol,
                direction=sig.direction.value,
                entry_time=entry_ts_py,
                exit_time=ts_py,
                entry_price=entry_price,
                exit_price=exit_price,
                stop_loss=sl,
                take_profit=tp,
                position_size=sig.position_size,
                risk_amount=sig.risk_amount,
                pnl=round(net_pnl, 2),
                rr_achieved=round(rr, 3),
                trade_duration_minutes=duration,
                exit_reason="eod",
                fvg_type=sig.fvg.type.value if sig.fvg else "",
                sweep_type=sig.sweep_event.level_type.value if sig.sweep_event else "",
            )
        return None

    def _check_exit(
        self, row: pd.Series, active: dict, bar_idx: int, bars: pd.DataFrame
    ) -> Optional[dict]:
        """Used by trade_replay: check if active trade exits on this bar."""
        is_long = active["direction"] == "long"
        sl = active["stop_loss"]
        tp = active["take_profit"]
        entry = active["entry_price"]
        size = active["position_size"]

        lo = float(row["low"])
        hi = float(row["high"])

        exit_price: Optional[float] = None
        exit_reason = ""

        if is_long:
            if lo <= sl:
                exit_price = sl
                exit_reason = "sl"
            elif hi >= tp:
                exit_price = tp
                exit_reason = "tp"
        else:
            if hi >= sl:
                exit_price = sl
                exit_reason = "sl"
            elif lo <= tp:
                exit_price = tp
                exit_reason = "tp"

        if exit_price is None:
            return None

        if is_long:
            pnl = (exit_price - entry) * size
        else:
            pnl = (entry - exit_price) * size

        return {
            "exit_price": exit_price,
            "exit_reason": exit_reason,
            "pnl": round(pnl - self._commission * size * 2, 2),
        }

    @staticmethod
    def _normalise(bars: pd.DataFrame) -> pd.DataFrame:
        df = bars.copy()
        df.columns = [c.lower() for c in df.columns]
        if not hasattr(df.index, "tz") or df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        elif str(df.index.tz) != "UTC":
            df.index = df.index.tz_convert("UTC")
        return df
