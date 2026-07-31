"""Fail-closed entry gate and the paper-only emergency stop.

`can_open_new_entry` implements the spec's fail-closed control list: a new entry is rejected when
ANY blocking condition holds. The emergency stop blocks new entries while leaving existing-position
management untouched. Managing/closing existing positions is governed separately and is never
blocked by these controls — abandoning a position or its stop is not an option the system offers.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EntryContext:
    paper_endpoint_verified: bool
    data_fresh: bool
    broker_reconciled: bool
    tc2000_batch_fresh: bool
    db_ok: bool
    all_positions_have_stops: bool
    risk_limit_ok: bool               # daily + portfolio risk within limits
    buying_power_ok: bool
    regular_session: bool
    clock_ok: bool
    no_duplicate_intent: bool
    emergency_stop: bool = False


# Each check maps a predicate-name to a human blocker reason emitted when it fails.
_CHECKS = [
    ("paper_endpoint_verified", "endpoint_not_verified_paper"),
    ("data_fresh", "market_data_stale_or_unavailable"),
    ("broker_reconciled", "broker_state_unreconciled"),
    ("tc2000_batch_fresh", "tc2000_batch_stale_or_invalid"),
    ("db_ok", "database_persistence_failed"),
    ("all_positions_have_stops", "existing_position_missing_stop"),
    ("risk_limit_ok", "risk_limit_reached"),
    ("buying_power_ok", "insufficient_buying_power"),
    ("regular_session", "market_closed_or_unsupported_session"),
    ("clock_ok", "clock_drift_exceeds_tolerance"),
    ("no_duplicate_intent", "duplicate_order_intent"),
]


@dataclass
class EntryDecision:
    allowed: bool
    blockers: list[str] = field(default_factory=list)


def can_open_new_entry(ctx: EntryContext) -> EntryDecision:
    """Allow a new entry only if every fail-closed control passes and no emergency stop is set."""
    blockers: list[str] = []
    if ctx.emergency_stop:
        blockers.append("emergency_stop_active")
    for attr, reason in _CHECKS:
        if not getattr(ctx, attr):
            blockers.append(reason)
    return EntryDecision(allowed=not blockers, blockers=blockers)


def can_manage_existing_positions() -> bool:
    """Managing and closing existing positions (stops, partials, exits) is ALWAYS permitted,
    including during an emergency stop or graceful shutdown."""
    return True
