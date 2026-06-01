"""
Signal-to-trade bridge diagnostics for PAPER_EVAL_PERMISSIVE_ENTRY_MODE.

Persists actual gate values vs thresholds for every signal evaluated
when permissive entry mode is enabled.  Advisory only — does not alter
strategy logic, thresholds, or position sizing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class BridgeEntry:
    """One signal evaluated through the full gate chain."""
    session_date: str
    timestamp: datetime
    symbol: str
    strategy_id: str
    signal_direction: str
    signal_age_seconds: float = 0.0
    universe_group: Optional[str] = None

    # Scanner context
    scanner_score: Optional[float] = None
    scanner_approved: Optional[bool] = None

    # Signal quality
    signal_quality_score: float = 0.0
    confluence_count: int = 1

    # Option contract (populated once liquidity filter selects a contract)
    option_contract: Optional[str] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    spread_pct: Optional[float] = None
    spread_threshold: Optional[float] = None

    # Liquidity — actual values and thresholds
    rvol: Optional[float] = None
    rvol_threshold: Optional[float] = None
    option_volume: Optional[int] = None
    option_volume_threshold: Optional[int] = None
    open_interest: Optional[int] = None
    open_interest_threshold: Optional[int] = None

    # Gate results
    liquidity_passed: Optional[bool] = None
    spread_passed: Optional[bool] = None
    risk_passed: Optional[bool] = None
    reconciliation_passed: bool = True
    position_limit_passed: bool = True

    # Underlying price at signal time (for ORB forward performance)
    underlying_price_at_signal: Optional[float] = None

    # ORB slot reservation flag
    orb_slot_reserved: bool = False

    # ORB forward performance (filled post-session)
    orb_fwd_price_5m: Optional[float] = None
    orb_fwd_price_15m: Optional[float] = None
    orb_fwd_price_30m: Optional[float] = None
    orb_fwd_pct_5m: Optional[float] = None
    orb_fwd_pct_15m: Optional[float] = None
    orb_fwd_pct_30m: Optional[float] = None

    # Final decision
    final_decision: str = "blocked"   # traded | blocked | skipped
    exact_block_reason: Optional[str] = None


async def persist_bridge_entries(entries: list[BridgeEntry], db_session) -> None:
    """Bulk-insert BridgeEntry rows into DBSignalBridge."""
    if not entries:
        return
    try:
        from app.api.models import DBSignalBridge
        rows = [
            DBSignalBridge(
                session_date=e.session_date,
                timestamp=e.timestamp,
                symbol=e.symbol,
                universe_group=e.universe_group,
                strategy_id=e.strategy_id,
                signal_direction=e.signal_direction,
                signal_age_seconds=e.signal_age_seconds,
                scanner_score=e.scanner_score,
                scanner_approved=e.scanner_approved,
                signal_quality_score=e.signal_quality_score,
                confluence_count=e.confluence_count,
                option_contract=e.option_contract,
                bid=e.bid,
                ask=e.ask,
                spread_pct=e.spread_pct,
                spread_threshold=e.spread_threshold,
                rvol=e.rvol,
                rvol_threshold=e.rvol_threshold,
                option_volume=e.option_volume,
                option_volume_threshold=e.option_volume_threshold,
                open_interest=e.open_interest,
                open_interest_threshold=e.open_interest_threshold,
                liquidity_passed=e.liquidity_passed,
                spread_passed=e.spread_passed,
                risk_passed=e.risk_passed,
                reconciliation_passed=e.reconciliation_passed,
                position_limit_passed=e.position_limit_passed,
                underlying_price_at_signal=e.underlying_price_at_signal,
                orb_slot_reserved=e.orb_slot_reserved,
                final_decision=e.final_decision,
                exact_block_reason=e.exact_block_reason,
            )
            for e in entries
        ]
        db_session.add_all(rows)
        await db_session.commit()
        logger.debug("Bridge diagnostics: persisted %d entries", len(rows))
    except Exception as exc:
        logger.warning("Failed to persist bridge entries: %s", exc)
