"""
Options Liquidity Filter.

Applied to an OptionChain to select the best contract for a given signal.

Selection criteria (in order of priority):
  1. Option type matches signal direction (call for LONG, put for SHORT).
  2. Delta closest to target range [delta_target_min, delta_target_max].
     If greeks are unavailable, use moneyness as a proxy.
  3. Open interest ≥ min_open_interest.
  4. Volume ≥ min_volume.
  5. Bid/ask spread ≤ max_spread_pct.
  6. Among qualifying contracts, prefer the one with the tightest spread.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional

from ..brokers.broker_interface import OptionChain, OptionContract
from .strategy_base import Signal, SignalDirection

logger = logging.getLogger(__name__)


class LiquidityFilter:

    def __init__(self, params: Dict[str, Any] | None = None):
        params = params or {}
        self._min_oi: int = params.get("min_open_interest", 100)
        self._min_vol: int = params.get("min_volume", 50)
        self._max_spread_pct: float = params.get("max_spread_pct", 0.10)
        self._delta_min: float = params.get("delta_target_min", 0.35)
        self._delta_max: float = params.get("delta_target_max", 0.45)

    def select_contract(
        self,
        chain: OptionChain,
        signal: Signal,
    ) -> Optional[OptionContract]:
        """
        Select the most appropriate option contract for a signal.

        Returns None if no qualifying contract is found.
        """
        if signal.direction == SignalDirection.LONG:
            candidates = chain.calls
        elif signal.direction == SignalDirection.SHORT:
            candidates = chain.puts
        else:
            return None

        qualifying = [c for c in candidates if self._passes_liquidity(c)]

        if not qualifying:
            logger.info(
                "LiquidityFilter: no qualifying contracts for %s %s | "
                "total_candidates=%d",
                chain.symbol,
                signal.direction,
                len(candidates),
            )
            return None

        # Rank by delta proximity, then spread tightness
        target_delta = (self._delta_min + self._delta_max) / 2
        underlying = float(chain.underlying_price)

        def score(c: OptionContract) -> float:
            # Lower = better
            if c.delta is not None:
                delta_dist = abs(abs(c.delta) - target_delta)
            else:
                # Proxy when broker doesn't provide delta (e.g. Alpaca paper).
                # Signed moneyness from the buyer's perspective:
                #   LONG call: positive = OTM (delta < 0.5)
                #   SHORT put: positive = OTM (delta < 0.5)
                if signal.direction == SignalDirection.LONG:
                    signed_m = (float(c.strike) - underlying) / max(underlying, 1)
                else:
                    signed_m = (underlying - float(c.strike)) / max(underlying, 1)
                # Rough linear approximation: 0% OTM → delta≈0.5, 10% OTM → delta≈0
                delta_estimate = max(0.01, min(0.99, 0.5 - 5.0 * signed_m))
                delta_dist = abs(delta_estimate - target_delta)
            spread_penalty = c.spread_pct * 0.5
            return delta_dist + spread_penalty

        best = min(qualifying, key=score)
        logger.info(
            "LiquidityFilter: selected %s | delta=%s | spread_pct=%.3f | OI=%d | vol=%d",
            best.option_symbol,
            f"{best.delta:.3f}" if best.delta is not None else "N/A",
            best.spread_pct,
            best.open_interest,
            best.volume,
        )
        return best

    def _passes_liquidity(self, contract: OptionContract) -> bool:
        reasons = []

        if contract.open_interest < self._min_oi:
            reasons.append(f"OI {contract.open_interest} < {self._min_oi}")

        if contract.volume < self._min_vol:
            reasons.append(f"vol {contract.volume} < {self._min_vol}")

        if contract.spread_pct > self._max_spread_pct:
            reasons.append(f"spread {contract.spread_pct:.3f} > {self._max_spread_pct:.3f}")

        if contract.ask <= 0:
            reasons.append("zero ask")

        if reasons:
            logger.debug(
                "LiquidityFilter: rejected %s — %s",
                contract.option_symbol,
                "; ".join(reasons),
            )
            return False
        return True

    def filter_chain(self, chain: OptionChain) -> OptionChain:
        """Return a new chain with only liquid contracts."""
        from dataclasses import replace
        return OptionChain(
            symbol=chain.symbol,
            expiration=chain.expiration,
            underlying_price=chain.underlying_price,
            calls=[c for c in chain.calls if self._passes_liquidity(c)],
            puts=[c for c in chain.puts if self._passes_liquidity(c)],
            fetched_at=chain.fetched_at,
        )
