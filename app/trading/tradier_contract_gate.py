"""Fail-closed Tradier validation for a contract selected from Alpaca data.

The gate is intentionally separate from the observation-only market-data observer.
It never places orders and never substitutes a contract.  When explicitly enabled,
it may only approve or veto the exact contract already selected by the normal
liquidity filter.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx

from app.evaluation.market_data_observer import build_comparison

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class TradierGateDecision:
    enabled: bool
    passed: bool
    reason: str
    comparison: Optional[dict[str, Any]] = None
    quote: Optional[dict[str, Any]] = None


class TradierContractGate:
    """Validate one exact option contract against a current Tradier quote."""

    def __init__(
        self,
        *,
        token: Optional[str],
        base_url: str = "https://api.tradier.com/v1",
        mode: str = "off",
        timeout_seconds: float = 5.0,
        max_quote_age_seconds: float = 10.0,
        fail_closed: bool = True,
        require_delta: bool = True,
        output_file: str = "./evaluation/market_data_comparisons.jsonl",
    ) -> None:
        self._token = (token or "").strip()
        self._base_url = base_url.rstrip("/")
        self._mode = mode.strip().lower()
        self._timeout = max(1.0, float(timeout_seconds))
        self._max_quote_age = max(1.0, float(max_quote_age_seconds))
        self._fail_closed = bool(fail_closed)
        self._require_delta = bool(require_delta)
        self._output = Path(output_file)

    @classmethod
    def from_env(cls) -> "TradierContractGate":
        return cls(
            token=os.getenv("TRADIER_MARKET_DATA_TOKEN"),
            base_url=os.getenv(
                "TRADIER_MARKET_DATA_BASE_URL",
                "https://api.tradier.com/v1",
            ),
            mode=os.getenv("TRADIER_CONTRACT_GATE_MODE", "off"),
            timeout_seconds=float(
                os.getenv("TRADIER_CONTRACT_GATE_TIMEOUT_SECS", "5")
            ),
            max_quote_age_seconds=float(
                os.getenv("TRADIER_CONTRACT_GATE_MAX_QUOTE_AGE_SECS", "10")
            ),
            fail_closed=_as_bool(
                os.getenv("TRADIER_CONTRACT_GATE_FAIL_CLOSED"),
                default=True,
            ),
            require_delta=_as_bool(
                os.getenv("TRADIER_CONTRACT_GATE_REQUIRE_DELTA"),
                default=True,
            ),
            output_file=os.getenv(
                "MARKET_DATA_COMPARISON_FILE",
                "./evaluation/market_data_comparisons.jsonl",
            ),
        )

    @property
    def enabled(self) -> bool:
        return self._mode == "veto"

    def validate_selected_contract(
        self,
        *,
        contract: Any,
        underlying_symbol: str,
        strategy_id: str,
        direction: str,
        thresholds: dict[str, Any],
    ) -> TradierGateDecision:
        """Approve or veto the exact selected contract; never return a replacement."""
        if not self.enabled:
            return TradierGateDecision(False, True, "gate_disabled")

        option_symbol = str(getattr(contract, "option_symbol", "") or "")
        if not option_symbol:
            return TradierGateDecision(True, False, "missing_option_symbol")

        if not self._token:
            reason = "Tradier contract gate enabled but token is missing"
            decision = TradierGateDecision(
                True,
                not self._fail_closed,
                reason,
            )
            self._write_decision(
                contract=contract,
                underlying_symbol=underlying_symbol,
                strategy_id=strategy_id,
                direction=direction,
                thresholds=thresholds,
                decision=decision,
            )
            return decision

        try:
            quote, rate_limit = self._fetch_quote(option_symbol)
            observed_at = datetime.now(tz=timezone.utc)
            alpaca = {
                "feed": os.getenv("ALPACA_OPTIONS_FEED_LABEL", "unspecified"),
                "bid": _float(getattr(contract, "bid", None)),
                "ask": _float(getattr(contract, "ask", None)),
                "volume": int(getattr(contract, "volume", 0) or 0),
                "open_interest": int(getattr(contract, "open_interest", 0) or 0),
                "implied_volatility": _float(
                    getattr(contract, "implied_volatility", None)
                ),
                "delta": _float(getattr(contract, "delta", None)),
            }
            comparison = build_comparison(
                alpaca=alpaca,
                tradier=quote,
                thresholds=thresholds,
                observed_at=observed_at,
            )
            reasons = list(comparison.get("tradier_rejection_reasons") or [])
            quote_age = comparison.get("tradier_quote_age_secs")
            if quote_age is None:
                reasons.append("Tradier quote timestamp unavailable")
            elif float(quote_age) > self._max_quote_age:
                reasons.append(
                    f"Tradier quote age {float(quote_age):.1f}s > {self._max_quote_age:.1f}s"
                )

            tradier_delta = _float((quote.get("greeks") or {}).get("delta"))
            if self._require_delta and tradier_delta is None:
                reasons.append("Tradier delta unavailable")

            passed = not reasons
            reason = "passed" if passed else "; ".join(dict.fromkeys(reasons))
            decision = TradierGateDecision(
                True,
                passed,
                reason,
                comparison=comparison,
                quote=quote,
            )
            self._write_decision(
                contract=contract,
                underlying_symbol=underlying_symbol,
                strategy_id=strategy_id,
                direction=direction,
                thresholds=thresholds,
                decision=decision,
                rate_limit=rate_limit,
            )
            return decision
        except Exception as exc:
            reason = f"Tradier validation error: {type(exc).__name__}: {exc}"
            decision = TradierGateDecision(
                True,
                not self._fail_closed,
                reason[:300],
            )
            self._write_decision(
                contract=contract,
                underlying_symbol=underlying_symbol,
                strategy_id=strategy_id,
                direction=direction,
                thresholds=thresholds,
                decision=decision,
            )
            return decision

    def _fetch_quote(
        self,
        option_symbol: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }
        with httpx.Client(
            base_url=self._base_url,
            headers=headers,
            timeout=self._timeout,
        ) as client:
            response = client.get(
                "/markets/quotes",
                params={"symbols": option_symbol, "greeks": "true"},
            )
            response.raise_for_status()
            quote = (response.json().get("quotes") or {}).get("quote")
            if isinstance(quote, list):
                quote = next(
                    (row for row in quote if row.get("symbol") == option_symbol),
                    None,
                )
            if not isinstance(quote, dict):
                raise ValueError(f"Tradier returned no quote for {option_symbol}")
            rate_limit = {
                "allowed": response.headers.get("X-Ratelimit-Allowed"),
                "used": response.headers.get("X-Ratelimit-Used"),
                "available": response.headers.get("X-Ratelimit-Available"),
            }
            return quote, rate_limit

    def _write_decision(
        self,
        *,
        contract: Any,
        underlying_symbol: str,
        strategy_id: str,
        direction: str,
        thresholds: dict[str, Any],
        decision: TradierGateDecision,
        rate_limit: Optional[dict[str, Any]] = None,
    ) -> None:
        try:
            now = datetime.now(tz=timezone.utc)
            quote = decision.quote or {}
            row = {
                "event": "tradier_contract_gate_decision",
                "ts": now.isoformat(),
                "session_date": now.astimezone(ET).date().isoformat(),
                "mode": "veto",
                "affects_trading": True,
                "underlying_symbol": underlying_symbol,
                "option_symbol": str(
                    getattr(contract, "option_symbol", "") or ""
                ),
                "strategy_id": strategy_id,
                "direction": direction,
                "passed": decision.passed,
                "reason": decision.reason,
                "thresholds": dict(thresholds),
                "tradier": {
                    "bid": _float(quote.get("bid")),
                    "ask": _float(quote.get("ask")),
                    "volume": int(quote.get("volume") or 0),
                    "open_interest": int(quote.get("open_interest") or 0),
                    "delta": _float((quote.get("greeks") or {}).get("delta")),
                    "implied_volatility": _float(
                        (quote.get("greeks") or {}).get("smv_vol")
                    ),
                    "bid_date": quote.get("bid_date"),
                    "ask_date": quote.get("ask_date"),
                },
                "comparison": decision.comparison,
                "rate_limit": rate_limit or {},
            }
            self._output.parent.mkdir(parents=True, exist_ok=True)
            with self._output.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, separators=(",", ":")) + "\n")
        except Exception as exc:
            logger.warning("Could not write Tradier contract gate telemetry: %s", exc)


@lru_cache(maxsize=1)
def get_tradier_contract_gate() -> TradierContractGate:
    return TradierContractGate.from_env()
