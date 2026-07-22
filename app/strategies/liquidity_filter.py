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
        # Optional cost cap: skip contracts whose ask * 100 exceeds this value.
        # Prevents deep-ITM contracts (e.g. delta=N/A, bid~$79) from being
        # selected and then rejected every cycle by the risk manager.
        self._max_contract_cost: Optional[float] = params.get("max_contract_cost", None)

    def set_max_contract_cost(self, max_cost: float) -> None:
        """Update the per-contract cost ceiling (call after equity is known)."""
        self._max_contract_cost = max_cost

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

        # Observation only: compare the already-selected Alpaca contract with a
        # Tradier production quote in a background task. The observer cannot
        # return a replacement contract or alter this method's result.
        try:
            from app.evaluation.market_data_observer import get_market_data_observer

            get_market_data_observer().submit_selected_contract(
                contract=best,
                underlying_symbol=chain.symbol,
                strategy_id=signal.strategy_id,
                direction=signal.direction.value,
                thresholds={
                    "min_open_interest": self._min_oi,
                    "min_volume": self._min_vol,
                    "max_spread_pct": self._max_spread_pct,
                    "delta_target_min": self._delta_min,
                    "delta_target_max": self._delta_max,
                    "max_contract_cost": self._max_contract_cost,
                },
            )
        except Exception as exc:
            logger.debug("Market-data observer scheduling failed: %s", exc)

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

        # Reject contracts outside the configured delta fallback band.
        # Uses abs(delta) so puts (negative delta) are compared by magnitude.
        if contract.delta is not None:
            d = abs(contract.delta)
            # Fallback band: ±0.10 around the configured target range.
            _lo = max(0.0, self._delta_min - 0.10)
            _hi = self._delta_max + 0.10
            if d < _lo or d > _hi:
                reasons.append(
                    f"delta abs={d:.3f} outside fallback band [{_lo:.2f}, {_hi:.2f}]"
                )

        # Reject deeply ITM contracts: delta=None with ask * 100 above the cost cap.
        # These have unmeasurable greeks and would always be rejected by risk anyway.
        if (
            contract.delta is None
            and self._max_contract_cost is not None
            and float(contract.ask) * 100 > self._max_contract_cost
        ):
            reasons.append(
                f"delta=N/A and cost ${float(contract.ask) * 100:.0f} > cap ${self._max_contract_cost:.0f}"
            )

        if reasons:
            logger.debug(
                "LiquidityFilter: rejected %s — %s",
                contract.option_symbol,
                "; ".join(reasons),
            )
            return False
        return True

    def classify_no_contract_reason(self, chain: OptionChain, signal: Signal) -> str:
        """
        Classify why select_contract returned None.

        Returns 'liquidity_cost_cap' when one or more contracts pass OI/volume/spread
        but are blocked by the per-contract cost ceiling (delta=None, ask*100 > cap).
        Returns 'liquidity_filter_no_contract' for all other cases.
        """
        if self._max_contract_cost is None:
            return "liquidity_filter_no_contract"

        candidates = chain.calls if signal.direction == SignalDirection.LONG else chain.puts
        for c in candidates:
            passes_basic = (
                c.open_interest >= self._min_oi
                and c.volume >= self._min_vol
                and c.spread_pct <= self._max_spread_pct
                and c.ask > 0
            )
            if passes_basic and c.delta is None and float(c.ask) * 100 > self._max_contract_cost:
                return "liquidity_cost_cap"
        return "liquidity_filter_no_contract"

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
