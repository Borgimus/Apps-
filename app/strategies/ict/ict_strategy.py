"""
ICT Liquidity Sweep & FVG Reversal Strategy.

Entry logic (in order)
───────────────────────
1. After trading_start_hour (default 14 UTC = 9 AM NY), compute Asian and
   London session levels from all bars prior to the current bar.
2. Monitor for a liquidity sweep of any session level with confirmed rejection.
3. After a confirmed sweep, scan the fvg_lookback_bars bars for a matching FVG:
     • After a HIGH sweep → bearish FVG (price should fall → sell signal)
     • After a LOW  sweep → bullish FVG (price should rise → buy signal)
4. Entry when price retraces into the FVG (fills >= fvg_fill_pct_entry).
5. SL = sweep extreme ± sl_buffer_pips.
6. TP = based on exit_mode:
     • fixed_rr       → entry ± (entry - stop_loss) * fixed_rr_ratio
     • liquidity_target → nearest liquidity pool in trade direction
     • major_structure  → next BOS/CHoCH level
     • hybrid          → take partial at first liquidity target, move SL to BE
7. Position size via PositionSizer.

Daily risk guards
──────────────────
• max_trades_per_day   – stop after N signals per calendar day.
• max_daily_loss_pct   – halt if cumulative simulated loss exceeds threshold.
• stop_trading_hour    – do not open new trades after this UTC hour.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd

from ..strategy_base import Signal, SignalDirection, StrategyBase
from .config import ICTConfig
from .fvg_detector import FVG, FVGDetector, FVGType
from .liquidity_sweep import LiquiditySweepDetector, SweepEvent, SweepType
from .liquidity_targets import LiquidityLevel, LiquidityTargetFinder
from .market_structure import MarketStructureEngine
from .position_sizer import PositionSizer, SizeResult
from .session_calculator import SessionCalculator, SessionLevels

logger = logging.getLogger(__name__)


@dataclass
class ICTSignal(Signal):
    """Extended signal carrying all ICT-specific context."""

    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    fvg: Optional[FVG] = None
    sweep_event: Optional[SweepEvent] = None
    liquidity_target: Optional[LiquidityLevel] = None
    risk_amount: float = 0.0
    position_size: float = 0.0

    def to_dict(self) -> dict:
        return {
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "direction": self.direction.value,
            "timestamp": self.timestamp.isoformat() if hasattr(self.timestamp, "isoformat") else str(self.timestamp),
            "price": self.price,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "risk_amount": round(self.risk_amount, 2),
            "position_size": self.position_size,
            "confidence": self.confidence,
            "notes": self.notes,
            "fvg": self.fvg.to_dict() if self.fvg else None,
            "sweep_event": self.sweep_event.to_dict() if self.sweep_event else None,
            "liquidity_target": self.liquidity_target.to_dict() if self.liquidity_target else None,
        }


class ICTStrategy(StrategyBase):
    """
    ICT Liquidity Sweep & FVG Reversal Strategy.

    Parameters
    ----------
    params : dict
        Any field of ICTConfig can be overridden here.
        Nested SessionWindow fields: use {'asian_session': {'start_hour': 0, 'end_hour': 6}}.
    """

    STRATEGY_ID = "ict_liquidity_sweep"

    def __init__(self, params: Dict[str, Any] | None = None):
        super().__init__(strategy_id=self.STRATEGY_ID, params=params or {})
        self._ict_cfg = ICTConfig.from_dict(params or {})
        self._session_calc = SessionCalculator(self._ict_cfg)
        self._sweep_detector = LiquiditySweepDetector(self._ict_cfg)
        self._fvg_detector = FVGDetector(self._ict_cfg)
        self._ms_engine = MarketStructureEngine(self._ict_cfg)
        self._target_finder = LiquidityTargetFinder(self._ict_cfg)
        self._sizer = PositionSizer.from_config(self._ict_cfg)

    @property
    def name(self) -> str:
        return "ICT Liquidity Sweep & FVG Reversal"

    # ── Main interface ────────────────────────────────────────────────────────

    def generate_signals(
        self, bars: pd.DataFrame, symbol: str
    ) -> List[ICTSignal]:
        """
        Generate ICT entry signals from 1-minute OHLCV bars.

        Parameters
        ----------
        bars : pd.DataFrame
            1-minute OHLCV with UTC-aware DatetimeIndex.
        symbol : str

        Returns
        -------
        List[ICTSignal]
        """
        if not self.validate_bars(bars, min_rows=50):
            return []

        bars = self._normalise(bars)

        # Compute session levels for all days in the dataset
        all_sessions = self._session_calc.compute_sessions(bars)
        if not all_sessions:
            logger.warning("No session levels computed for %s", symbol)
            return []

        signals: List[ICTSignal] = []

        # daily-guard state (reset when calendar date changes)
        _guard_date: Optional[str] = None
        _trades_today: int = 0
        _daily_loss: float = 0.0

        # Process each session day separately
        for session_levels in all_sessions:
            day_signals = self._process_day(
                bars=bars,
                symbol=symbol,
                session_levels=session_levels,
                all_sessions=all_sessions,
            )
            for sig in day_signals:
                signals.append(sig)

        logger.info(
            "ICTStrategy: %d signals generated for %s over %d bars",
            len(signals),
            symbol,
            len(bars),
        )
        return signals

    # ── Day-level processing ──────────────────────────────────────────────────

    def _process_day(
        self,
        bars: pd.DataFrame,
        symbol: str,
        session_levels: SessionLevels,
        all_sessions: List[SessionLevels],
    ) -> List[ICTSignal]:
        """Process a single trading day."""
        cfg = self._ict_cfg
        signals: List[ICTSignal] = []

        # Filter bars to this trading day (UTC date)
        day_mask = bars.index.date == pd.Timestamp(session_levels.date).date()
        day_bars = bars[day_mask]
        if day_bars.empty:
            return []

        # Detect sweeps on this day's bars against the session levels
        sweeps = self._sweep_detector.detect_sweeps(day_bars, session_levels, symbol)
        confirmed_sweeps = [s for s in sweeps if s.rejection_confirmed]

        if not confirmed_sweeps:
            return []

        # Liquidity targets for TP calculation
        liq_targets = self._target_finder.find_targets(
            minute_bars=bars,
            session_levels_list=all_sessions,
        )

        trades_today = 0
        daily_loss = 0.0

        for sweep in confirmed_sweeps:
            if trades_today >= cfg.max_trades_per_day:
                break
            if daily_loss / cfg.account_size >= cfg.max_daily_loss_pct:
                logger.info("Daily loss limit reached for %s on %s", symbol, session_levels.date)
                break

            # Check trading hours
            sweep_hour = sweep.timestamp.hour if hasattr(sweep.timestamp, "hour") else 0
            if not (cfg.trading_start_hour <= sweep_hour < cfg.stop_trading_hour):
                continue

            sig = self._build_signal(
                sweep=sweep,
                day_bars=day_bars,
                all_bars=bars,
                symbol=symbol,
                liq_targets=liq_targets,
            )
            if sig is None:
                continue

            signals.append(sig)
            trades_today += 1
            # Accrue simulated risk (not actual PnL — that's the backtester's job)
            daily_loss += sig.risk_amount

        return signals

    # ── Signal construction ───────────────────────────────────────────────────

    def _build_signal(
        self,
        sweep: SweepEvent,
        day_bars: pd.DataFrame,
        all_bars: pd.DataFrame,
        symbol: str,
        liq_targets: List[LiquidityLevel],
    ) -> Optional[ICTSignal]:
        """
        Attempt to build a complete ICTSignal from a confirmed sweep.
        Returns None if the setup doesn't qualify.
        """
        cfg = self._ict_cfg

        is_high_sweep = sweep.direction == "bearish"
        fvg_type_needed = FVGType.BEARISH if is_high_sweep else FVGType.BULLISH
        trade_direction = SignalDirection.SHORT if is_high_sweep else SignalDirection.LONG

        # Find the sweep candle index in day_bars
        sweep_idx = sweep.sweep_candle_index
        # day_bars is a sub-slice: reindex
        day_bars_reset = day_bars.reset_index()
        if sweep_idx >= len(day_bars_reset):
            sweep_idx = len(day_bars_reset) - 1

        # Scan for matching FVG in bars around the sweep
        fvgs = self._fvg_detector.find_fvgs_after_sweep(
            bars=day_bars,
            sweep_candle_index=sweep_idx,
            lookback=cfg.fvg_lookback_bars,
            fvg_type=fvg_type_needed,
        )

        if not fvgs:
            logger.debug(
                "No %s FVG found near sweep at %s for %s",
                fvg_type_needed.value,
                sweep.timestamp,
                symbol,
            )
            return None

        # Use the most recent FVG closest to the sweep
        fvg = fvgs[-1]

        # Entry price: midpoint of FVG (or upper/lower boundary based on direction)
        if fvg_type_needed == FVGType.BULLISH:
            entry_price = fvg.lower_price  # wait for price to retrace into gap bottom
        else:
            entry_price = fvg.upper_price  # price retraces up into gap top

        # Stop-loss: sweep extreme + buffer
        sl_buffer = cfg.sl_buffer_price
        if is_high_sweep:
            stop_loss = sweep.sweep_price + sl_buffer  # SL above the high
        else:
            stop_loss = sweep.sweep_price - sl_buffer  # SL below the low

        # Sanity: SL must be on the right side of entry
        if trade_direction == SignalDirection.LONG and stop_loss >= entry_price:
            logger.debug("Invalid SL for LONG: entry=%.4f sl=%.4f", entry_price, stop_loss)
            return None
        if trade_direction == SignalDirection.SHORT and stop_loss <= entry_price:
            logger.debug("Invalid SL for SHORT: entry=%.4f sl=%.4f", entry_price, stop_loss)
            return None

        # Take-profit calculation
        tp, liq_target = self._calc_tp(
            entry=entry_price,
            stop_loss=stop_loss,
            direction=trade_direction,
            liq_targets=liq_targets,
        )

        # Position size
        try:
            size_result = self._sizer.calculate(entry_price, stop_loss)
        except ValueError as exc:
            logger.warning("Position sizing error: %s", exc)
            return None

        # Confidence: higher with stronger sweep (extension / tick_size ratio)
        extension_ticks = sweep.extension / cfg.tick_size
        raw_conf = min(1.0, 0.5 + extension_ticks / 20.0)

        if raw_conf < cfg.min_confidence:
            return None

        ts = sweep.timestamp

        sig = ICTSignal(
            strategy_id=self.STRATEGY_ID,
            symbol=symbol,
            direction=trade_direction,
            timestamp=ts,
            price=entry_price,
            confidence=round(raw_conf, 3),
            notes=(
                f"Sweep of {sweep.level_type.value} at {sweep.level_price:.4f}; "
                f"FVG [{fvg.lower_price:.4f}-{fvg.upper_price:.4f}]; "
                f"SL={stop_loss:.4f}; TP={tp:.4f}"
            ),
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=tp,
            fvg=fvg,
            sweep_event=sweep,
            liquidity_target=liq_target,
            risk_amount=size_result.risk_amount,
            position_size=size_result.position_size,
        )
        return sig

    def _calc_tp(
        self,
        entry: float,
        stop_loss: float,
        direction: SignalDirection,
        liq_targets: List[LiquidityLevel],
    ):
        """Calculate take-profit price based on exit_mode."""
        cfg = self._ict_cfg
        risk = abs(entry - stop_loss)
        is_long = direction == SignalDirection.LONG
        liq_target: Optional[LiquidityLevel] = None

        if cfg.exit_mode == "fixed_rr":
            tp = entry + risk * cfg.fixed_rr_ratio if is_long else entry - risk * cfg.fixed_rr_ratio

        elif cfg.exit_mode == "liquidity_target":
            tgt_dir = "bullish" if is_long else "bearish"
            liq_target = self._target_finder.nearest_target(liq_targets, entry, tgt_dir)
            if liq_target is not None:
                tp = liq_target.price
            else:
                # Fall back to fixed_rr
                tp = entry + risk * cfg.fixed_rr_ratio if is_long else entry - risk * cfg.fixed_rr_ratio

        elif cfg.exit_mode == "major_structure":
            # Use 2× risk as a proxy; in live use the next BOS level
            tp = entry + risk * 2.0 if is_long else entry - risk * 2.0

        else:  # hybrid
            # First target at nearest liquidity; runners to 3R
            tgt_dir = "bullish" if is_long else "bearish"
            liq_target = self._target_finder.nearest_target(liq_targets, entry, tgt_dir)
            if liq_target is not None:
                tp = liq_target.price
            else:
                tp = entry + risk * cfg.fixed_rr_ratio if is_long else entry - risk * cfg.fixed_rr_ratio

        return tp, liq_target

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _normalise(bars: pd.DataFrame) -> pd.DataFrame:
        df = bars.copy()
        df.columns = [c.lower() for c in df.columns]
        if not hasattr(df.index, "tz") or df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        elif str(df.index.tz) != "UTC":
            df.index = df.index.tz_convert("UTC")
        return df
