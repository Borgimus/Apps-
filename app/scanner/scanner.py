"""
Multi-symbol ICT signal scanner.

Runs the ICT strategy across a list of symbols concurrently using asyncio.
Each symbol is scanned in a thread-pool executor to keep the async loop free.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from ..strategies.ict import (
    FVG,
    ICTSignal,
    ICTStrategy,
    SessionCalculator,
    SessionLevels,
)
from ..strategies.ict.config import ICTConfig

logger = logging.getLogger(__name__)


@dataclass
class ScanResult:
    symbol: str
    signal: Optional[ICTSignal]
    confidence: float
    session_levels: Optional[SessionLevels]
    active_fvgs: List[FVG]
    scanned_at: datetime = field(default_factory=lambda: datetime.utcnow())
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "signal": self.signal.to_dict() if self.signal else None,
            "confidence": round(self.confidence, 3),
            "session_levels": self.session_levels.to_dict() if self.session_levels else None,
            "active_fvgs": [f.to_dict() for f in self.active_fvgs],
            "scanned_at": self.scanned_at.isoformat(),
            "error": self.error,
        }


# Type alias for the data fetcher callback
# DataFetcher(symbol) -> pd.DataFrame  (1-minute OHLCV, UTC index)
DataFetcher = Callable[[str], pd.DataFrame]


class ICTScanner:
    """
    Multi-symbol ICT scanner.

    Parameters
    ----------
    symbols : list[str]
        Ticker symbols to scan.
    data_fetcher : callable
        Function that accepts a symbol string and returns a UTC-indexed
        1-minute OHLCV DataFrame.  May be synchronous or async.
    params : dict | None
        ICTStrategy params to apply to all symbols.
    max_concurrent : int
        Maximum number of concurrent scans (thread-pool workers).
    """

    def __init__(
        self,
        symbols: List[str],
        data_fetcher: DataFetcher,
        params: Dict[str, Any] | None = None,
        max_concurrent: int = 4,
    ):
        self._symbols = list(symbols)
        self._fetcher = data_fetcher
        self._params = params or {}
        self._max_concurrent = max_concurrent
        self._strategy = ICTStrategy(params)
        self._session_calc = SessionCalculator(ICTConfig.from_dict(params or {}))

    # ── Public API ────────────────────────────────────────────────────────────

    async def scan_all(self) -> List[ScanResult]:
        """
        Scan all symbols concurrently.

        Returns
        -------
        List[ScanResult] in the same order as self._symbols.
        """
        semaphore = asyncio.Semaphore(self._max_concurrent)

        async def _bounded_scan(symbol: str) -> ScanResult:
            async with semaphore:
                return await self._scan_symbol(symbol)

        tasks = [_bounded_scan(sym) for sym in self._symbols]
        results = await asyncio.gather(*tasks, return_exceptions=False)
        logger.info(
            "Scanner completed %d symbols; %d have signals",
            len(results),
            sum(1 for r in results if r.signal is not None),
        )
        return list(results)

    async def scan_symbol(self, symbol: str) -> ScanResult:
        """Scan a single symbol."""
        return await self._scan_symbol(symbol)

    def scan_all_sync(self) -> List[ScanResult]:
        """Synchronous wrapper around scan_all (creates its own event loop)."""
        return asyncio.run(self.scan_all())

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _scan_symbol(self, symbol: str) -> ScanResult:
        loop = asyncio.get_event_loop()
        try:
            bars = await loop.run_in_executor(None, self._fetcher, symbol)
        except Exception as exc:
            logger.error("Data fetch failed for %s: %s", symbol, exc)
            return ScanResult(
                symbol=symbol,
                signal=None,
                confidence=0.0,
                session_levels=None,
                active_fvgs=[],
                error=str(exc),
            )

        if bars is None or bars.empty:
            return ScanResult(
                symbol=symbol,
                signal=None,
                confidence=0.0,
                session_levels=None,
                active_fvgs=[],
                error="Empty bar data",
            )

        try:
            result = await loop.run_in_executor(
                None, self._analyse, symbol, bars
            )
        except Exception as exc:
            logger.error("Analysis failed for %s: %s", symbol, exc, exc_info=True)
            return ScanResult(
                symbol=symbol,
                signal=None,
                confidence=0.0,
                session_levels=None,
                active_fvgs=[],
                error=str(exc),
            )

        return result

    def _analyse(self, symbol: str, bars: pd.DataFrame) -> ScanResult:
        """Runs in thread pool — no async."""
        signals = self._strategy.generate_signals(bars, symbol)
        session_levels_list = self._session_calc.compute_sessions(bars)
        latest_session = session_levels_list[-1] if session_levels_list else None

        # Find the most recent (highest confidence) signal
        best_signal: Optional[ICTSignal] = None
        if signals:
            best_signal = max(signals, key=lambda s: s.confidence)

        # Collect active (unfilled) FVGs from the last 50 bars
        from ..strategies.ict import FVGDetector
        from ..strategies.ict.config import ICTConfig
        fvg_det = FVGDetector(ICTConfig.from_dict(self._params))
        recent_bars = bars.iloc[-50:] if len(bars) > 50 else bars
        recent_bars = recent_bars.copy()
        recent_bars.columns = [c.lower() for c in recent_bars.columns]
        all_fvgs = fvg_det.detect_fvgs(recent_bars)
        last_price = float(bars["close"].iloc[-1]) if not bars.empty else 0.0
        active_fvgs = [f for f in all_fvgs if f.check_fill(last_price) < 1.0]

        return ScanResult(
            symbol=symbol,
            signal=best_signal,
            confidence=best_signal.confidence if best_signal else 0.0,
            session_levels=latest_session,
            active_fvgs=active_fvgs,
        )
