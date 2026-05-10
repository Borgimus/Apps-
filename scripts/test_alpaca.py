"""
Quick Alpaca connectivity test — account, quote, option chain.
Run: python scripts/test_alpaca.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio
import warnings
warnings.filterwarnings("ignore")


async def main():
    from app.config import get_settings
    from app.brokers import get_broker

    s = get_settings()
    print(f"Broker  : {s.broker}")
    print(f"Base URL: {s.alpaca_base_url}")
    print(f"Key ID  : {s.alpaca_api_key[:6]}...{s.alpaca_api_key[-4:]}")
    print(f"Live    : {s.live_trading_enabled}")
    print()

    broker = get_broker(s)

    # 1 — Account
    print("── Account ──────────────────────────────────────")
    acct = await broker.get_account()
    print(f"  ID          : {acct.account_id}")
    print(f"  Equity      : ${acct.equity:,.2f}")
    print(f"  Cash        : ${acct.cash:,.2f}")
    print(f"  Buying power: ${acct.buying_power:,.2f}")
    print(f"  Paper       : {acct.is_paper}")

    # 2 — Quote
    print("\n── SPY Quote ─────────────────────────────────────")
    quote = await broker.get_quote("SPY")
    print(f"  Bid: ${quote.bid}  Ask: ${quote.ask}  Mid: ${quote.mid:.2f}")
    print(f"  Volume: {quote.volume:,}  @ {quote.timestamp}")

    # 3 — Positions
    print("\n── Open Positions ────────────────────────────────")
    positions = await broker.get_positions()
    if positions:
        for p in positions:
            print(f"  {p.symbol}  qty={p.quantity}  avg=${p.avg_cost}  pnl=${p.unrealized_pnl}")
    else:
        print("  None")

    # 4 — Expirations
    print("\n── SPY Option Expirations (next 5) ───────────────")
    try:
        exps = await broker.get_available_expirations("SPY")
        for exp in exps[:5]:
            print(f"  {exp}")
    except Exception as e:
        print(f"  Error: {e}")

    await broker.close()
    print("\nConnection test passed.")


if __name__ == "__main__":
    asyncio.run(main())
