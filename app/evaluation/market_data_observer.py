"""Observation-only comparison of selected Alpaca option contracts with Tradier.

This module never returns market data to the trading path and exposes no order
methods.  It schedules best-effort background quote checks and writes JSONL
telemetry for later review.  Missing credentials, request failures, and rate
limits are recorded or ignored without affecting contract selection or orders.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")


def _float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _spread_pct(bid: Optional[float], ask: Optional[float]) -> Optional[float]:
    if bid is None or ask is None:
        return None
    mid = (bid + ask) / 2
    if mid <= 0:
        return None
    return (ask - bid) / mid


def _mid(bid: Optional[float], ask: Optional[float]) -> Optional[float]:
    if bid is None or ask is None or bid < 0 or ask <= 0:
        return None
    return (bid + ask) / 2


def _epoch_ms(value: Any) -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _quote_object(payload: dict[str, Any], option_symbol: str) -> dict[str, Any]:
    quote = (payload.get("quotes") or {}).get("quote")
    if isinstance(quote, list):
        quote = next((row for row in quote if row.get("symbol") == option_symbol), None)
    if not isinstance(quote, dict):
        raise ValueError(f"Tradier returned no quote for {option_symbol}")
    return quote


def build_comparison(
    *,
    alpaca: dict[str, Any],
    tradier: dict[str, Any],
    thresholds: dict[str, Any],
    observed_at: datetime,
) -> dict[str, Any]:
    """Build deterministic comparison fields from two quote snapshots."""
    a_bid = _float(alpaca.get("bid"))
    a_ask = _float(alpaca.get("ask"))
    t_bid = _float(tradier.get("bid"))
    t_ask = _float(tradier.get("ask"))
    a_mid = _mid(a_bid, a_ask)
    t_mid = _mid(t_bid, t_ask)
    a_spread = _spread_pct(a_bid, a_ask)
    t_spread = _spread_pct(t_bid, t_ask)

    bid_ts = _epoch_ms(tradier.get("bid_date"))
    ask_ts = _epoch_ms(tradier.get("ask_date"))
    quote_ts = max((ts for ts in (bid_ts, ask_ts) if ts is not None), default=None)
    quote_age = None
    if quote_ts is not None:
        quote_age = max(0.0, (observed_at.astimezone(timezone.utc) - quote_ts).total_seconds())

    reasons: list[str] = []
    min_oi = int(thresholds.get("min_open_interest") or 0)
    min_volume = int(thresholds.get("min_volume") or 0)
    max_spread = _float(thresholds.get("max_spread_pct"))
    delta_min = _float(thresholds.get("delta_target_min"))
    delta_max = _float(thresholds.get("delta_target_max"))
    max_cost = _float(thresholds.get("max_contract_cost"))

    t_oi = int(tradier.get("open_interest") or 0)
    t_volume = int(tradier.get("volume") or 0)
    t_delta = _float((tradier.get("greeks") or {}).get("delta"))

    if t_oi < min_oi:
        reasons.append(f"OI {t_oi} < {min_oi}")
    if t_volume < min_volume:
        reasons.append(f"volume {t_volume} < {min_volume}")
    if t_ask is None or t_ask <= 0:
        reasons.append("zero ask")
    if max_spread is not None and (t_spread is None or t_spread > max_spread):
        reasons.append(
            "spread unavailable" if t_spread is None else f"spread {t_spread:.3f} > {max_spread:.3f}"
        )
    if t_delta is not None and delta_min is not None and delta_max is not None:
        lower = max(0.0, delta_min - 0.10)
        upper = delta_max + 0.10
        if not lower <= abs(t_delta) <= upper:
            reasons.append(f"delta abs={abs(t_delta):.3f} outside [{lower:.2f}, {upper:.2f}]")
    if t_delta is None and max_cost is not None and t_ask is not None and t_ask * 100 > max_cost:
        reasons.append(f"delta unavailable and cost ${t_ask * 100:.0f} > cap ${max_cost:.0f}")

    tradier_passed = not reasons
    return {
        "alpaca_mid": a_mid,
        "tradier_mid": t_mid,
        "mid_diff": None if a_mid is None or t_mid is None else round(t_mid - a_mid, 6),
        "mid_diff_pct": (
            None if a_mid in (None, 0) or t_mid is None else round((t_mid - a_mid) / a_mid, 6)
        ),
        "bid_diff": None if a_bid is None or t_bid is None else round(t_bid - a_bid, 6),
        "ask_diff": None if a_ask is None or t_ask is None else round(t_ask - a_ask, 6),
        "alpaca_spread_pct": a_spread,
        "tradier_spread_pct": t_spread,
        "spread_diff_pct": (
            None if a_spread is None or t_spread is None else round(t_spread - a_spread, 6)
        ),
        "tradier_quote_timestamp": quote_ts.isoformat() if quote_ts else None,
        "tradier_quote_age_secs": round(quote_age, 3) if quote_age is not None else None,
        "tradier_liquidity_passed": tradier_passed,
        "liquidity_disagreement": not tradier_passed,
        "tradier_rejection_reasons": reasons,
    }


class TradierMarketDataObserver:
    """Best-effort, throttled observer for pre-entry selected contracts."""

    def __init__(
        self,
        *,
        token: Optional[str],
        base_url: str = "https://api.tradier.com/v1",
        mode: str = "off",
        output_file: str = "./evaluation/market_data_comparisons.jsonl",
        throttle_seconds: float = 60.0,
        request_timeout: float = 5.0,
        alpaca_feed_label: str = "unspecified",
    ) -> None:
        self._token = (token or "").strip()
        self._base_url = base_url.rstrip("/")
        self._mode = mode.strip().lower()
        self._output = Path(output_file)
        self._throttle = max(1.0, float(throttle_seconds))
        self._timeout = max(1.0, float(request_timeout))
        self._alpaca_feed_label = alpaca_feed_label.strip() or "unspecified"
        self._last_submit: dict[tuple[str, str], float] = {}
        self._tasks: set[asyncio.Task] = set()
        self._write_lock: Optional[asyncio.Lock] = None

    @classmethod
    def from_env(cls) -> "TradierMarketDataObserver":
        return cls(
            token=os.getenv("TRADIER_MARKET_DATA_TOKEN"),
            base_url=os.getenv("TRADIER_MARKET_DATA_BASE_URL", "https://api.tradier.com/v1"),
            mode=os.getenv("TRADIER_MARKET_DATA_MODE", "off"),
            output_file=os.getenv(
                "MARKET_DATA_COMPARISON_FILE",
                "./evaluation/market_data_comparisons.jsonl",
            ),
            throttle_seconds=float(os.getenv("TRADIER_MARKET_DATA_THROTTLE_SECS", "60")),
            request_timeout=float(os.getenv("TRADIER_MARKET_DATA_TIMEOUT_SECS", "5")),
            alpaca_feed_label=os.getenv("ALPACA_OPTIONS_FEED_LABEL", "unspecified"),
        )

    @property
    def enabled(self) -> bool:
        return self._mode == "observe" and bool(self._token)

    def submit_selected_contract(
        self,
        *,
        contract: Any,
        underlying_symbol: str,
        strategy_id: str,
        direction: str,
        thresholds: dict[str, Any],
    ) -> bool:
        """Schedule a comparison and return immediately; never raise to trading."""
        if not self.enabled:
            return False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False

        option_symbol = str(getattr(contract, "option_symbol", "") or "")
        if not option_symbol:
            return False
        key = (option_symbol, strategy_id)
        now_mono = time.monotonic()
        if now_mono - self._last_submit.get(key, 0.0) < self._throttle:
            return False
        self._last_submit[key] = now_mono

        alpaca = {
            "feed": self._alpaca_feed_label,
            "bid": _float(getattr(contract, "bid", None)),
            "ask": _float(getattr(contract, "ask", None)),
            "volume": int(getattr(contract, "volume", 0) or 0),
            "open_interest": int(getattr(contract, "open_interest", 0) or 0),
            "implied_volatility": _float(getattr(contract, "implied_volatility", None)),
            "delta": _float(getattr(contract, "delta", None)),
        }
        task = loop.create_task(
            self._observe(
                option_symbol=option_symbol,
                underlying_symbol=underlying_symbol,
                strategy_id=strategy_id,
                direction=direction,
                alpaca=alpaca,
                thresholds=dict(thresholds),
            )
        )
        self._tasks.add(task)
        task.add_done_callback(self._task_done)
        return True

    def _task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # defensive: task failures never reach trading
            logger.debug("Tradier observer background task failed: %s", exc)

    async def _observe(
        self,
        *,
        option_symbol: str,
        underlying_symbol: str,
        strategy_id: str,
        direction: str,
        alpaca: dict[str, Any],
        thresholds: dict[str, Any],
    ) -> None:
        observed_at = datetime.now(tz=timezone.utc)
        base = {
            "event": "selected_contract_comparison",
            "ts": observed_at.isoformat(),
            "session_date": observed_at.astimezone(ET).date().isoformat(),
            "mode": "observe",
            "affects_trading": False,
            "underlying_symbol": underlying_symbol,
            "option_symbol": option_symbol,
            "strategy_id": strategy_id,
            "direction": direction,
            "alpaca": alpaca,
            "thresholds": thresholds,
        }
        try:
            tradier, rate = await self._fetch_quote(option_symbol)
            row = {
                **base,
                "status": "ok",
                "tradier": {
                    "environment": "production" if "sandbox" not in self._base_url else "sandbox",
                    "bid": _float(tradier.get("bid")),
                    "ask": _float(tradier.get("ask")),
                    "volume": int(tradier.get("volume") or 0),
                    "open_interest": int(tradier.get("open_interest") or 0),
                    "implied_volatility": _float((tradier.get("greeks") or {}).get("smv_vol")),
                    "delta": _float((tradier.get("greeks") or {}).get("delta")),
                    "bid_date": tradier.get("bid_date"),
                    "ask_date": tradier.get("ask_date"),
                },
                "comparison": build_comparison(
                    alpaca=alpaca,
                    tradier=tradier,
                    thresholds=thresholds,
                    observed_at=observed_at,
                ),
                "rate_limit": rate,
            }
        except Exception as exc:
            row = {
                **base,
                "status": "error",
                "error_type": type(exc).__name__,
                "error": str(exc)[:300],
            }
        await self._append(row)

    async def _fetch_quote(self, option_symbol: str) -> tuple[dict[str, Any], dict[str, Any]]:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }
        async with httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            timeout=self._timeout,
        ) as client:
            response = await client.get(
                "/markets/quotes",
                params={"symbols": option_symbol, "greeks": "true"},
            )
            response.raise_for_status()
            quote = _quote_object(response.json(), option_symbol)
            rate = {
                "allowed": response.headers.get("X-Ratelimit-Allowed"),
                "used": response.headers.get("X-Ratelimit-Used"),
                "available": response.headers.get("X-Ratelimit-Available"),
            }
            return quote, rate

    async def _append(self, row: dict[str, Any]) -> None:
        if self._write_lock is None:
            self._write_lock = asyncio.Lock()
        async with self._write_lock:
            try:
                self._output.parent.mkdir(parents=True, exist_ok=True)
                with self._output.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row, separators=(",", ":"), default=str) + "\n")
            except OSError as exc:
                logger.debug("Tradier observer write failed: %s", exc)


_OBSERVER: Optional[TradierMarketDataObserver] = None


def get_market_data_observer() -> TradierMarketDataObserver:
    global _OBSERVER
    if _OBSERVER is None:
        _OBSERVER = TradierMarketDataObserver.from_env()
        if _OBSERVER.enabled:
            logger.info("Tradier market-data observer enabled (read-only)")
    return _OBSERVER


def reset_market_data_observer_for_tests() -> None:
    global _OBSERVER
    _OBSERVER = None
