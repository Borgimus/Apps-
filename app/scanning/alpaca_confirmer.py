"""
Alpaca Confirmation Layer — verifies option chain liquidity before trading.

After yfinance scoring selects a candidate, this layer:
  1. Fetches Alpaca expirations for the symbol
  2. Finds the nearest preferred DTE
  3. Fetches the Alpaca option chain
  4. Runs LiquidityFilter to select the best contract
  5. Verifies quote freshness, spread, OI, volume, and delta target
  6. Returns a ConfirmedCandidate (with verified contract) or None

Alpaca is always the execution source of truth.
yfinance candidate data is for screening only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import List, Optional
from zoneinfo import ZoneInfo

from ..brokers.broker_interface import OptionContract
from ..strategies.liquidity_filter import LiquidityFilter
from ..strategies.strategy_base import Signal, SignalDirection
from .candidate_scorer import CandidateScore

logger = logging.getLogger(__name__)
_ET = ZoneInfo("America/New_York")


@dataclass
class ConfirmedCandidate:
    candidate: CandidateScore
    contract: OptionContract
    expiration: date
    quote_freshness_secs: float
    rejection_reason: Optional[str]       # set when confirm() returns None
    confirmed_at: datetime = field(default_factory=lambda: datetime.now(tz=_ET))

    @property
    def symbol(self) -> str:
        return self.candidate.symbol

    @property
    def score(self) -> float:
        return self.candidate.score

    @property
    def signal_type(self) -> str:
        return self.candidate.signal_type


class AlpacaConfirmer:
    """
    Confirms yfinance candidates against live Alpaca option chain data.

    Usage:
        confirmer = AlpacaConfirmer(broker, settings)
        confirmed = await confirmer.confirm(candidate)
        if confirmed is not None:
            # confirmed.contract is the Alpaca-verified option contract
    """

    def __init__(self, broker, settings):
        self._broker = broker
        self._settings = settings
        self._liq_filter = LiquidityFilter({
            "min_open_interest": settings.risk.min_open_interest,
            "min_volume":        settings.risk.min_volume,
            "max_spread_pct":    settings.risk.max_spread_pct,
            "delta_target_min":  settings.options.delta_target_min,
            "delta_target_max":  settings.options.delta_target_max,
        })

    def set_max_contract_cost(self, max_cost: float) -> None:
        """Forward cost cap to internal liquidity filter (call after equity is known)."""
        self._liq_filter.set_max_contract_cost(max_cost)

    async def confirm(self, candidate: CandidateScore) -> Optional[ConfirmedCandidate]:
        """
        Confirm a candidate via Alpaca.
        Returns ConfirmedCandidate on success, None on failure.
        """
        symbol = candidate.symbol

        if candidate.is_rejected:
            logger.debug("AlpacaConfirmer: skipping rejected candidate %s", symbol)
            return None

        if candidate.signal_type == "NEUTRAL":
            logger.debug("AlpacaConfirmer: skipping NEUTRAL signal for %s", symbol)
            return None

        # ── Find preferred expiration ─────────────────────────────────────────
        try:
            expirations = await self._broker.get_available_expirations(symbol)
        except Exception as exc:
            logger.warning("AlpacaConfirmer: cannot get expirations for %s: %s", symbol, exc)
            return None

        if not expirations:
            logger.info("AlpacaConfirmer: no expirations available for %s", symbol)
            return None

        today = datetime.now(tz=_ET).date()
        target_exp = self._pick_expiration(expirations, today)
        if target_exp is None:
            logger.info("AlpacaConfirmer: no suitable expiration for %s", symbol)
            return None

        # ── Fetch Alpaca option chain ─────────────────────────────────────────
        try:
            chain = await self._broker.get_option_chain(symbol, target_exp)
        except Exception as exc:
            logger.warning("AlpacaConfirmer: cannot fetch chain for %s %s: %s", symbol, target_exp, exc)
            return None

        # ── Quote freshness ───────────────────────────────────────────────────
        freshness_secs = 0.0
        if chain.fetched_at:
            freshness_secs = (datetime.now(tz=_ET) - chain.fetched_at.astimezone(_ET)).total_seconds()
            if freshness_secs > 120:
                logger.warning(
                    "AlpacaConfirmer: stale chain for %s (%.0f s old)", symbol, freshness_secs
                )
                return None

        # ── Liquidity filter via Alpaca chain ─────────────────────────────────
        direction = (
            SignalDirection.LONG if candidate.signal_type == "LONG" else SignalDirection.SHORT
        )
        dummy_signal = Signal(
            strategy_id="alpaca_confirmer",
            symbol=symbol,
            direction=direction,
            timestamp=datetime.now(tz=_ET),
            price=candidate.metrics.price,
        )
        contract = self._liq_filter.select_contract(chain, dummy_signal)
        if contract is None:
            _max_cost = self._liq_filter._max_contract_cost
            if _max_cost is not None:
                _candidates = chain.calls if direction == SignalDirection.LONG else chain.puts
                for _c in _candidates:
                    if (
                        _c.delta is None
                        and float(_c.ask) * 100 > _max_cost
                        and _c.open_interest >= self._settings.risk.min_open_interest
                        and _c.volume >= self._settings.risk.min_volume
                        and _c.spread_pct <= self._settings.risk.max_spread_pct
                    ):
                        logger.info(
                            "AlpacaConfirmer: liquidity_rejected_cost_cap | symbol=%s | "
                            "option_symbol=%s | ask=%.2f | ask_x_100=%.0f | max_contract_cost=%.0f",
                            symbol, _c.option_symbol,
                            float(_c.ask), float(_c.ask) * 100, _max_cost,
                        )
            logger.info(
                "AlpacaConfirmer: no liquid contract for %s (signal=%s)", symbol, candidate.signal_type
            )
            return None

        # ── Final spread / delta checks ───────────────────────────────────────
        max_spread = self._settings.risk.max_spread_pct
        if contract.spread_pct > max_spread:
            logger.info(
                "AlpacaConfirmer: spread %.2f%% > max %.2f%% for %s",
                contract.spread_pct * 100, max_spread * 100, contract.option_symbol,
            )
            return None

        logger.info(
            "AlpacaConfirmer: confirmed %s | %s | exp=%s | spread=%.2f%% | OI=%d | vol=%d",
            symbol, contract.option_symbol, target_exp,
            contract.spread_pct * 100, contract.open_interest, contract.volume,
        )

        return ConfirmedCandidate(
            candidate=candidate,
            contract=contract,
            expiration=target_exp,
            quote_freshness_secs=freshness_secs,
            rejection_reason=None,
        )

    async def confirm_all(
        self, candidates: List[CandidateScore]
    ) -> List[ConfirmedCandidate]:
        """Confirm all non-rejected candidates; return only successfully confirmed ones."""
        import asyncio
        results = await asyncio.gather(
            *[self.confirm(c) for c in candidates],
            return_exceptions=False,
        )
        return [r for r in results if r is not None]

    def _pick_expiration(self, expirations: List[date], today: date) -> Optional[date]:
        preferred_dte = list(self._settings.options.preferred_dte)
        for dte in preferred_dte:
            candidate = today + timedelta(days=dte)
            if candidate in expirations:
                return candidate
        # Fall back to nearest available
        future = [e for e in expirations if e >= today]
        if future:
            return min(future, key=lambda e: (e - today).days)
        return None
