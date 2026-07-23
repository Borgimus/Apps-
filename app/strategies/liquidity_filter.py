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
  7. When explicitly enabled, Tradier may veto the exact selected contract.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

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
        self._max_contract_cost: Optional[float] = params.get(
            "max_contract_cost",
            None,
        )
        self._last_rejection_reason: Optional[str] = None

    def set_max_contract_cost(self, max_cost: float) -> None:
        """Update the per-contract cost ceiling (call after equity is known)."""
        self._max_contract_cost = max_cost

    def _thresholds(self) -> dict[str, Any]:
        return {
            "min_open_interest": self._min_oi,
            "min_volume": self._min_vol,
            "max_spread_pct": self._max_spread_pct,
            "delta_target_min": self._delta_min,
            "delta_target_max": self._delta_max,
            "max_contract_cost": self._max_contract_cost,
        }

    def select_contract(
        self,
        chain: OptionChain,
        signal: Signal,
    ) -> Optional[OptionContract]:
        """
        Select the most appropriate option contract for a signal.

        Returns None if no qualifying contract is found or if an enabled
        Tradier safety gate vetoes the selected contract.
        """
        self._last_rejection_reason = None

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

        # Rank by delta proximity, then spread tightness.
        target_delta = (self._delta_min + self._delta_max) / 2
        underlying = float(chain.underlying_price)

        def score(c: OptionContract) -> float:
            # Lower = better.
            if c.delta is not None:
                delta_dist = abs(abs(c.delta) - target_delta)
            else:
                # Proxy when broker doesn't provide delta (e.g. Alpaca paper).
                # Signed moneyness from the buyer's perspective:
                #   LONG call: positive = OTM (delta < 0.5)
                #   SHORT put: positive = OTM (delta < 0.5)
                if signal.direction == SignalDirection.LONG:
                    signed_m = (
                        float(c.strike) - underlying
                    ) / max(underlying, 1)
                else:
                    signed_m = (
                        underlying - float(c.strike)
                    ) / max(underlying, 1)
                # Rough linear approximation: 0% OTM → delta≈0.5,
                # 10% OTM → delta≈0.
                delta_estimate = max(
                    0.01,
                    min(0.99, 0.5 - 5.0 * signed_m),
                )
                delta_dist = abs(delta_estimate - target_delta)
            spread_penalty = c.spread_pct * 0.5
            return delta_dist + spread_penalty

        best = min(qualifying, key=score)
        logger.info(
            "LiquidityFilter: selected %s | delta=%s | "
            "spread_pct=%.3f | OI=%d | vol=%d",
            best.option_symbol,
            f"{best.delta:.3f}" if best.delta is not None else "N/A",
            best.spread_pct,
            best.open_interest,
            best.volume,
        )

        thresholds = self._thresholds()

        # Preserve the independent observation stream even when the safety gate
        # is disabled or later vetoes the contract.
        try:
            from app.evaluation.market_data_observer import (
                get_market_data_observer,
            )

            get_market_data_observer().submit_selected_contract(
                contract=best,
                underlying_symbol=chain.symbol,
                strategy_id=signal.strategy_id,
                direction=signal.direction.value,
                thresholds=thresholds,
            )
        except Exception as exc:
            logger.debug(
                "Market-data observer scheduling failed: %s",
                exc,
            )

        # Explicit safety gate. It may only approve or veto this exact contract;
        # it cannot choose a replacement or place an order. When mode=veto, a
        # request failure is fail-closed by default.
        try:
            from app.trading.tradier_contract_gate import (
                get_tradier_contract_gate,
            )

            decision = get_tradier_contract_gate().validate_selected_contract(
                contract=best,
                underlying_symbol=chain.symbol,
                strategy_id=signal.strategy_id,
                direction=signal.direction.value,
                thresholds=thresholds,
            )
            if decision.enabled and not decision.passed:
                self._last_rejection_reason = "tradier_contract_veto"
                logger.warning(
                    "Tradier contract veto | %s | %s",
                    best.option_symbol,
                    decision.reason,
                )
                return None
        except Exception as exc:
            # The gate itself handles fail-open/fail-closed behavior. This outer
            # guard protects legacy/test environments where the module cannot be
            # constructed. An enabled gate should not normally reach this path.
            logger.error(
                "Tradier contract gate invocation failed for %s: %s",
                best.option_symbol,
                exc,
            )
            self._last_rejection_reason = "tradier_contract_gate_error"
            return None

        return best

    def _passes_liquidity(self, contract: OptionContract) -> bool:
        reasons = []

        if contract.open_interest < self._min_oi:
            reasons.append(
                f"OI {contract.open_interest} < {self._min_oi}"
            )

        if contract.volume < self._min_vol:
            reasons.append(
                f"vol {contract.volume} < {self._min_vol}"
            )

        if contract.spread_pct > self._max_spread_pct:
            reasons.append(
                f"spread {contract.spread_pct:.3f} > "
                f"{self._max_spread_pct:.3f}"
            )

        if contract.ask <= 0:
            reasons.append("zero ask")

        # Reject contracts outside the configured delta fallback band.
        # Uses abs(delta) so puts (negative delta) are compared by magnitude.
        if contract.delta is not None:
            d = abs(contract.delta)
            # Fallback band: ±0.10 around the configured target range.
            lower = max(0.0, self._delta_min - 0.10)
            upper = self._delta_max + 0.10
            if d < lower or d > upper:
                reasons.append(
                    f"delta abs={d:.3f} outside fallback band "
                    f"[{lower:.2f}, {upper:.2f}]"
                )

        # Reject deeply ITM contracts: delta=None with ask * 100 above the cost
        # cap. These have unmeasurable greeks and would always be rejected by
        # risk anyway.
        if (
            contract.delta is None
            and self._max_contract_cost is not None
            and float(contract.ask) * 100 > self._max_contract_cost
        ):
            reasons.append(
                f"delta=N/A and cost ${float(contract.ask) * 100:.0f} > "
                f"cap ${self._max_contract_cost:.0f}"
            )

        if reasons:
            logger.debug(
                "LiquidityFilter: rejected %s — %s",
                contract.option_symbol,
                "; ".join(reasons),
            )
            return False
        return True

    def classify_no_contract_reason(
        self,
        chain: OptionChain,
        signal: Signal,
    ) -> str:
        """Classify why ``select_contract`` returned ``None``."""
        if self._last_rejection_reason:
            return self._last_rejection_reason

        if self._max_contract_cost is None:
            return "liquidity_filter_no_contract"

        candidates = (
            chain.calls
            if signal.direction == SignalDirection.LONG
            else chain.puts
        )
        for contract in candidates:
            passes_basic = (
                contract.open_interest >= self._min_oi
                and contract.volume >= self._min_vol
                and contract.spread_pct <= self._max_spread_pct
                and contract.ask > 0
            )
            if (
                passes_basic
                and contract.delta is None
                and float(contract.ask) * 100 > self._max_contract_cost
            ):
                return "liquidity_cost_cap"
        return "liquidity_filter_no_contract"

    def filter_chain(self, chain: OptionChain) -> OptionChain:
        """Return a new chain with only liquid contracts."""
        return OptionChain(
            symbol=chain.symbol,
            expiration=chain.expiration,
            underlying_price=chain.underlying_price,
            calls=[
                contract
                for contract in chain.calls
                if self._passes_liquidity(contract)
            ],
            puts=[
                contract
                for contract in chain.puts
                if self._passes_liquidity(contract)
            ],
            fetched_at=chain.fetched_at,
        )
