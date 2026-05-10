"""
Backtest Engine.

Runs strategies against historical OHLCV data and generates performance reports.

IMPORTANT APPROXIMATION NOTES
──────────────────────────────
1. Options backtesting is done on *underlying* price data by default.
   If you provide real options price data, results will be more accurate.
2. When options data is missing, Black-Scholes is used to estimate option
   prices.  These results are clearly marked as APPROXIMATE.
3. Slippage, commission, and bid/ask spread assumptions are configurable.
4. Fill modeling assumes limit orders fill at the limit price + slippage
   on the first bar where the underlying moves through the strike level.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from ..config import get_settings
from ..data.yfinance_data import YFinanceDataSource
from ..strategies.strategy_base import Signal, SignalDirection, StrategyBase

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    strategy_id: str
    symbol: str
    direction: str
    entry_time: datetime
    exit_time: Optional[datetime]
    entry_price: float           # underlying price at entry
    exit_price: Optional[float]
    option_entry_price: float    # option premium paid/received
    option_exit_price: Optional[float]
    quantity: int
    gross_pnl: float
    commission: float
    slippage: float
    net_pnl: float
    is_approximate: bool = True   # True when using BS-estimated option prices


@dataclass
class BacktestResult:
    strategy_id: str
    symbol: str
    start_date: str
    end_date: str
    trades: List[TradeRecord] = field(default_factory=list)
    is_approximate: bool = True

    # Computed metrics (populated by compute_metrics)
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    total_pnl: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    expectancy: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    equity_curve: List[float] = field(default_factory=list)

    def compute_metrics(self, starting_equity: float = 100_000.0):
        self.total_trades = len(self.trades)
        if self.total_trades == 0:
            return

        pnls = [t.net_pnl for t in self.trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        self.winning_trades = len(wins)
        self.losing_trades = len(losses)
        self.win_rate = self.winning_trades / self.total_trades
        self.total_pnl = sum(pnls)
        self.avg_win = float(np.mean(wins)) if wins else 0.0
        self.avg_loss = float(np.mean(losses)) if losses else 0.0
        self.expectancy = (
            self.win_rate * self.avg_win + (1 - self.win_rate) * self.avg_loss
        )
        gross_wins = sum(wins)
        gross_losses = abs(sum(losses))
        self.profit_factor = gross_wins / gross_losses if gross_losses > 0 else float("inf")

        # Equity curve
        equity = starting_equity
        curve = [equity]
        for pnl in pnls:
            equity += pnl
            curve.append(equity)
        self.equity_curve = curve

        # Max drawdown
        peak = curve[0]
        max_dd = 0.0
        for val in curve:
            if val > peak:
                peak = val
            dd = (peak - val) / peak
            if dd > max_dd:
                max_dd = dd
        self.max_drawdown = max_dd

        # Sharpe ratio (annualised, assuming 252 trading days)
        if len(pnls) > 1 and np.std(pnls) > 0:
            trades_per_year = 252  # approximate
            pnl_arr = np.array(pnls) / starting_equity
            self.sharpe_ratio = float(
                np.mean(pnl_arr) / np.std(pnl_arr) * np.sqrt(trades_per_year)
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "is_approximate": self.is_approximate,
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": round(self.win_rate, 4),
            "profit_factor": round(self.profit_factor, 4),
            "total_pnl": round(self.total_pnl, 2),
            "avg_win": round(self.avg_win, 2),
            "avg_loss": round(self.avg_loss, 2),
            "expectancy": round(self.expectancy, 2),
            "max_drawdown": round(self.max_drawdown, 4),
            "sharpe_ratio": round(self.sharpe_ratio, 4),
        }

    def print_summary(self):
        data = self.to_dict()
        approx_note = " [⚠ APPROXIMATE — synthetic options pricing]" if self.is_approximate else ""
        lines = [
            f"\n{'='*60}",
            f"Backtest: {self.strategy_id} | {self.symbol}{approx_note}",
            f"Period  : {self.start_date} → {self.end_date}",
            f"{'─'*60}",
            f"Trades      : {data['total_trades']} "
            f"(W: {data['winning_trades']} / L: {data['losing_trades']})",
            f"Win Rate    : {data['win_rate']:.1%}",
            f"Profit Factor: {data['profit_factor']:.2f}",
            f"Total P&L   : ${data['total_pnl']:,.2f}",
            f"Avg Win     : ${data['avg_win']:,.2f}",
            f"Avg Loss    : ${data['avg_loss']:,.2f}",
            f"Expectancy  : ${data['expectancy']:,.2f}",
            f"Max Drawdown: {data['max_drawdown']:.1%}",
            f"Sharpe Ratio: {data['sharpe_ratio']:.2f}",
            f"{'='*60}\n",
        ]
        print("\n".join(lines))


class BacktestEngine:
    """
    Vectorised backtest engine.

    Runs strategies on daily or intraday bars.  Options pricing is either
    loaded from real data or approximated via Black-Scholes when unavailable.
    """

    def __init__(self, settings=None):
        self._s = settings or get_settings()
        self._data = YFinanceDataSource()
        self._bt_s = self._s.backtesting

    async def run(
        self,
        strategy: StrategyBase,
        symbol: str,
        start: str | None = None,
        end: str | None = None,
        interval: str = "1d",
        starting_equity: float = 100_000.0,
        dte: int = 1,
        target_delta: float = 0.40,
    ) -> BacktestResult:
        """
        Run a single strategy/symbol backtest.

        Parameters
        ----------
        dte          : simulated days-to-expiration for option selection.
        target_delta : delta used for Black-Scholes option price estimation.
        """
        start = start or self._bt_s.default_start
        end = end or self._bt_s.default_end

        logger.info(
            "Backtest: %s | %s | %s → %s | interval=%s",
            strategy.strategy_id,
            symbol,
            start,
            end,
            interval,
        )

        bars = await self._data.get_bars(symbol, start, end, interval)
        if bars.empty:
            logger.warning("No bars returned for %s — aborting backtest", symbol)
            return BacktestResult(
                strategy_id=strategy.strategy_id,
                symbol=symbol,
                start_date=start,
                end_date=end,
            )

        signals = strategy.generate_signals(bars, symbol)
        logger.info("Generated %d signals for %s", len(signals), symbol)

        trades = self._simulate_trades(
            signals=signals,
            bars=bars,
            symbol=symbol,
            dte=dte,
            target_delta=target_delta,
        )

        result = BacktestResult(
            strategy_id=strategy.strategy_id,
            symbol=symbol,
            start_date=start,
            end_date=end,
            trades=trades,
            is_approximate=True,  # always True when using yfinance / BS pricing
        )
        result.compute_metrics(starting_equity=starting_equity)

        if self._bt_s.warn_synthetic_options:
            logger.warning(
                "Backtest results for %s/%s use SYNTHETIC options pricing (Black-Scholes). "
                "Treat as approximations only.",
                strategy.strategy_id,
                symbol,
            )

        return result

    async def run_all(
        self,
        strategies: List[StrategyBase],
        symbols: List[str],
        start: str | None = None,
        end: str | None = None,
        **kwargs,
    ) -> List[BacktestResult]:
        """Run all strategy × symbol combinations."""
        results = []
        for strat in strategies:
            for sym in symbols:
                result = await self.run(strat, sym, start, end, **kwargs)
                results.append(result)
        return results

    def _simulate_trades(
        self,
        signals: List[Signal],
        bars: pd.DataFrame,
        symbol: str,
        dte: int,
        target_delta: float,
    ) -> List[TradeRecord]:
        """
        Pair entry signals with exits and compute P&L.

        Exit rules:
          • For daily bars: exit on the close of the next bar (1-bar hold).
          • For intraday bars: exit at end of same session.
        """
        trades: List[TradeRecord] = []
        bars_idx = {ts: i for i, ts in enumerate(bars.index)}
        commission = self._bt_s.commission_per_contract
        slippage = self._bt_s.slippage_per_contract
        spread_assumption = self._bt_s.options_spread_assumption

        for sig in signals:
            if not sig.is_actionable():
                continue

            # Find entry bar index
            entry_ts = pd.Timestamp(sig.timestamp)
            if entry_ts.tzinfo is None:
                entry_ts = entry_ts.tz_localize("UTC")
            # Find nearest bar at or after signal
            future = bars[bars.index >= entry_ts]
            if future.empty:
                continue

            entry_bar_idx = bars.index.get_loc(future.index[0])
            entry_price = float(bars.iloc[entry_bar_idx]["close"])

            # Estimate IV from historical volatility (research-grade only)
            try:
                hv_window = min(21, entry_bar_idx)
                if hv_window < 5:
                    iv = 0.20  # fallback
                else:
                    log_ret = np.log(
                        bars["close"].iloc[max(0, entry_bar_idx - hv_window) : entry_bar_idx + 1]
                        / bars["close"].iloc[max(0, entry_bar_idx - hv_window) : entry_bar_idx + 1].shift(1)
                    ).dropna()
                    iv = float(log_ret.std() * np.sqrt(252))
                    iv = max(iv, 0.05)
            except Exception:
                iv = 0.20

            T = dte / 365.0
            r = 0.05  # assumed risk-free rate

            # Estimate option price with Black-Scholes
            if sig.direction == SignalDirection.LONG:
                strike = entry_price * (1 + (1 - target_delta) * 0.05)
                opt_price = YFinanceDataSource.black_scholes_price(
                    entry_price, strike, T, r, iv, "call"
                )
            else:
                strike = entry_price * (1 - (1 - target_delta) * 0.05)
                opt_price = YFinanceDataSource.black_scholes_price(
                    entry_price, strike, T, r, iv, "put"
                )

            # Apply spread assumption (entry cost penalty)
            opt_entry = opt_price + spread_assumption / 2 + slippage
            quantity = 1  # 1 contract per trade in backtest

            # Find exit bar (next bar or end of day)
            exit_bar_idx = min(entry_bar_idx + 1, len(bars) - 1)
            exit_bar = bars.iloc[exit_bar_idx]
            exit_price = float(exit_bar["close"])
            exit_ts = bars.index[exit_bar_idx]

            # Estimate exit option price
            T_exit = max(T - 1 / 365.0, 0.0001)
            if sig.direction == SignalDirection.LONG:
                opt_exit = YFinanceDataSource.black_scholes_price(
                    exit_price, strike, T_exit, r, iv, "call"
                )
            else:
                opt_exit = YFinanceDataSource.black_scholes_price(
                    exit_price, strike, T_exit, r, iv, "put"
                )
            opt_exit = max(opt_exit - spread_assumption / 2 - slippage, 0)

            gross_pnl = (opt_exit - opt_entry) * 100 * quantity
            total_commission = commission * quantity * 2  # entry + exit
            net_pnl = gross_pnl - total_commission

            trades.append(
                TradeRecord(
                    strategy_id=sig.strategy_id,
                    symbol=symbol,
                    direction=sig.direction.value,
                    entry_time=sig.timestamp,
                    exit_time=exit_ts.to_pydatetime() if hasattr(exit_ts, "to_pydatetime") else exit_ts,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    option_entry_price=round(opt_entry, 4),
                    option_exit_price=round(opt_exit, 4),
                    quantity=quantity,
                    gross_pnl=round(gross_pnl, 2),
                    commission=round(total_commission, 2),
                    slippage=round(slippage * quantity * 2, 2),
                    net_pnl=round(net_pnl, 2),
                    is_approximate=True,
                )
            )

        return trades

    def save_report(self, result: BacktestResult, output_dir: str | None = None) -> str:
        """Save backtest results to CSV and print a text summary."""
        out = Path(output_dir or self._bt_s.output_dir)
        out.mkdir(parents=True, exist_ok=True)

        fname = f"{result.strategy_id}_{result.symbol}_{result.start_date}_{result.end_date}.csv"
        fpath = out / fname

        if result.trades:
            df = pd.DataFrame([
                {
                    "strategy": t.strategy_id,
                    "symbol": t.symbol,
                    "direction": t.direction,
                    "entry_time": t.entry_time,
                    "exit_time": t.exit_time,
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "option_entry": t.option_entry_price,
                    "option_exit": t.option_exit_price,
                    "quantity": t.quantity,
                    "gross_pnl": t.gross_pnl,
                    "commission": t.commission,
                    "net_pnl": t.net_pnl,
                    "approximate": t.is_approximate,
                }
                for t in result.trades
            ])
            df.to_csv(fpath, index=False)
            logger.info("Backtest report saved to %s", fpath)
        else:
            logger.warning("No trades to save for %s/%s", result.strategy_id, result.symbol)

        result.print_summary()
        return str(fpath)
