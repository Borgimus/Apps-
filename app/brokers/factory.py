"""
Broker factory — instantiates the correct adapter from settings.
"""

from __future__ import annotations

import logging

from ..config import get_settings
from .broker_interface import BrokerInterface

logger = logging.getLogger(__name__)


def get_broker(settings=None) -> BrokerInterface:
    if settings is None:
        settings = get_settings()

    broker_name = settings.broker.lower()
    is_live = settings.live_trading_enabled

    logger.info("Creating broker adapter | broker=%s | live=%s", broker_name, is_live)

    if broker_name == "alpaca":
        from .alpaca_broker import AlpacaBroker
        if not settings.alpaca_api_key or not settings.alpaca_secret_key:
            raise ValueError(
                "ALPACA_API_KEY and ALPACA_SECRET_KEY must be set to use the Alpaca broker."
            )
        return AlpacaBroker(
            api_key=settings.alpaca_api_key,
            secret_key=settings.alpaca_secret_key,
            base_url=settings.alpaca_base_url,
            is_paper=not is_live,
        )

    if broker_name == "tradier":
        from .tradier_broker import TradierBroker
        if not settings.tradier_access_token:
            raise ValueError(
                "TRADIER_ACCESS_TOKEN must be set to use the Tradier broker."
            )
        return TradierBroker(
            access_token=settings.tradier_access_token,
            base_url=settings.tradier_base_url,
            is_paper=not is_live,
        )

    if broker_name == "ibkr":
        from .ibkr_broker import IBKRBroker
        return IBKRBroker(
            host=settings.ibkr_host,
            port=settings.ibkr_port,
            client_id=settings.ibkr_client_id,
        )

    if broker_name == "paper":
        from .paper_broker import PaperBroker
        from ..data.yfinance_data import YFinanceDataSource
        broker = PaperBroker()
        broker.set_data_source(YFinanceDataSource())
        return broker

    raise ValueError(
        f"Unknown broker '{broker_name}'. Supported: alpaca, tradier, ibkr, paper"
    )
