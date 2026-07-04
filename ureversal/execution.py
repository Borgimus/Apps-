"""Alpaca execution engine — marketable limit orders with hard cancel rules.

Venue abstraction per §9: `ExecutionVenue` is the contract; `AlpacaEquityVenue`
is the only armed implementation (SPY shares, paper or live). Options and
futures venues are declared stubs so strategy code never needs to change when
a venue is added.

Live orders require BOTH `live.mode: live` in yaml AND the environment
variable LIVE_TRADING_ENABLED=true; anything else routes to Alpaca paper.
"""
from __future__ import annotations

import logging
import time as _time
from dataclasses import dataclass

from .config import Config

log = logging.getLogger("ureversal.execution")


@dataclass
class Fill:
    symbol: str
    qty: int
    side: str
    price: float
    order_id: str


class ExecutionVenue:
    """Order contract shared by every venue implementation."""

    def buy_marketable_limit(self, symbol: str, qty: int) -> Fill | None:
        raise NotImplementedError

    def sell_marketable_limit(self, symbol: str, qty: int) -> Fill | None:
        raise NotImplementedError

    def flatten(self, symbol: str) -> Fill | None:
        """Unconditional close of any open position (kill switch / hard exit)."""
        raise NotImplementedError

    def equity(self) -> float:
        raise NotImplementedError


class AlpacaEquityVenue(ExecutionVenue):
    def __init__(self, cfg: Config):
        if not cfg.alpaca_key:
            raise RuntimeError("ALPACA_API_KEY / ALPACA_SECRET_KEY not set")
        from alpaca.trading.client import TradingClient

        self.cfg = cfg
        want_live = cfg.live["mode"] == "live"
        self.is_live = want_live and cfg.live_trading_enabled
        if want_live and not cfg.live_trading_enabled:
            log.warning("live.mode=live but LIVE_TRADING_ENABLED!=true — using PAPER")
        self.client = TradingClient(cfg.alpaca_key, cfg.alpaca_secret,
                                    paper=not self.is_live)
        from alpaca.data.historical import StockHistoricalDataClient

        self.data = StockHistoricalDataClient(cfg.alpaca_key, cfg.alpaca_secret)
        self.offset_cap = float(cfg.execution["limit_offset_cap_usd"])
        self.fill_timeout_s = float(cfg.execution["fill_timeout_s"])

    # ── quotes ──

    def _touch(self, symbol: str) -> tuple[float, float]:
        from alpaca.data.enums import DataFeed
        from alpaca.data.requests import StockLatestQuoteRequest

        q = self.data.get_stock_latest_quote(
            StockLatestQuoteRequest(symbol_or_symbols=symbol,
                                    feed=DataFeed(self.cfg.feed))
        )[symbol]
        return float(q.bid_price), float(q.ask_price)

    # ── orders ──

    def _marketable(self, symbol: str, qty: int, side: str) -> Fill | None:
        """Submit limit at touch ± offset cap; poll; cancel if unfilled within
        fill_timeout_s. Immediate cancellation on failure — no chasing."""
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest

        bid, ask = self._touch(symbol)
        limit = round(ask + self.offset_cap, 2) if side == "buy" else round(bid - self.offset_cap, 2)
        order = self.client.submit_order(
            LimitOrderRequest(
                symbol=symbol, qty=qty,
                side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
                time_in_force=TimeInForce.DAY, limit_price=limit,
            )
        )
        deadline = _time.monotonic() + self.fill_timeout_s
        while _time.monotonic() < deadline:
            o = self.client.get_order_by_id(order.id)
            if o.status == "filled":
                return Fill(symbol, int(float(o.filled_qty)), side,
                            float(o.filled_avg_price), str(o.id))
            if o.status in ("canceled", "rejected", "expired"):
                log.warning("%s order %s: %s", side, o.id, o.status)
                return None
            _time.sleep(0.1)
        try:
            self.client.cancel_order_by_id(order.id)
        except Exception:
            pass
        o = self.client.get_order_by_id(order.id)
        if o.status == "filled":  # filled during the cancel race
            return Fill(symbol, int(float(o.filled_qty)), side,
                        float(o.filled_avg_price), str(o.id))
        if o.filled_qty and float(o.filled_qty) > 0:
            log.warning("partial fill %s/%s on %s — position carries the partial",
                        o.filled_qty, qty, o.id)
            return Fill(symbol, int(float(o.filled_qty)), side,
                        float(o.filled_avg_price), str(o.id))
        log.info("%s %d %s: unfilled in %.1fs — canceled", side, qty, symbol,
                 self.fill_timeout_s)
        return None

    def buy_marketable_limit(self, symbol: str, qty: int) -> Fill | None:
        return self._marketable(symbol, qty, "buy")

    def sell_marketable_limit(self, symbol: str, qty: int) -> Fill | None:
        return self._marketable(symbol, qty, "sell")

    def flatten(self, symbol: str) -> Fill | None:
        """Close position: try marketable limit once, then fall back to a
        market order — a hard exit must never be left working."""
        try:
            pos = self.client.get_open_position(symbol)
        except Exception:
            return None  # no position
        qty = int(float(pos.qty))
        if qty <= 0:
            return None
        fill = self.sell_marketable_limit(symbol, qty)
        if fill:
            return fill
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        log.warning("flatten fallback: MARKET sell %d %s", qty, symbol)
        o = self.client.submit_order(
            MarketOrderRequest(symbol=symbol, qty=qty, side=OrderSide.SELL,
                               time_in_force=TimeInForce.DAY)
        )
        for _ in range(50):
            oo = self.client.get_order_by_id(o.id)
            if oo.status == "filled":
                return Fill(symbol, qty, "sell", float(oo.filled_avg_price), str(oo.id))
            _time.sleep(0.1)
        log.error("flatten market order not confirmed filled: %s", o.id)
        return None

    def equity(self) -> float:
        return float(self.client.get_account().equity)


class SpyOptionsVenue(ExecutionVenue):
    """§9 hook: 0DTE delta-targeted SPY calls. NOT IMPLEMENTED — declared so
    the strategy/venue contract is fixed before any options work begins."""

    def __init__(self, cfg: Config):
        raise NotImplementedError("options venue is a declared hook; see STRATEGY_SPEC §9")


class FuturesVenue(ExecutionVenue):
    """§9 hook: ES/MES. Alpaca does not offer futures; this requires a
    futures-capable broker adapter. NOT IMPLEMENTED."""

    def __init__(self, cfg: Config):
        raise NotImplementedError("futures venue is a declared hook; see STRATEGY_SPEC §9")


def make_venue(cfg: Config) -> ExecutionVenue:
    kind = cfg.execution.get("venue", "alpaca_equity")
    return {
        "alpaca_equity": AlpacaEquityVenue,
        "spy_options": SpyOptionsVenue,
        "futures": FuturesVenue,
    }[kind](cfg)
