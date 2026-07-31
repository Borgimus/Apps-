"""Explicit strategy state machine with guarded, idempotent, timestamped transitions.

Every transition has: from-state, to-state, guard results, an idempotency key, a timestamp,
and a reason. Duplicate delivery of the same idempotency key is a no-op (returns the existing
transition). Restart recovery replays the persisted transition log to reach the same state.

Broker state wins for actual positions/orders; unresolved discrepancies drive RECON_BLOCKED,
and a filled position without a valid stop drives RISK_BLOCKED — both block new risk.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Callable


class State(str, Enum):
    IMPORTED = "IMPORTED"
    QUALIFIED = "QUALIFIED"
    SETUP_WATCH = "SETUP_WATCH"
    ENTRY_PENDING = "ENTRY_PENDING"
    OPEN_INITIAL_RISK = "OPEN_INITIAL_RISK"
    PARTIAL_PENDING = "PARTIAL_PENDING"
    OPEN_BREAKEVEN = "OPEN_BREAKEVEN"
    FINAL_EXIT_PENDING = "FINAL_EXIT_PENDING"
    CLOSED = "CLOSED"
    INVALIDATED = "INVALIDATED"
    RECON_BLOCKED = "RECON_BLOCKED"
    RISK_BLOCKED = "RISK_BLOCKED"


# Allowed forward transitions. Blocking states are reachable from any active state.
_ALLOWED: dict[State, set[State]] = {
    State.IMPORTED: {State.QUALIFIED, State.INVALIDATED},
    State.QUALIFIED: {State.SETUP_WATCH, State.INVALIDATED},
    State.SETUP_WATCH: {State.ENTRY_PENDING, State.INVALIDATED},
    State.ENTRY_PENDING: {State.OPEN_INITIAL_RISK, State.INVALIDATED},
    State.OPEN_INITIAL_RISK: {State.PARTIAL_PENDING, State.FINAL_EXIT_PENDING, State.CLOSED},
    State.PARTIAL_PENDING: {State.OPEN_BREAKEVEN, State.FINAL_EXIT_PENDING, State.CLOSED},
    State.OPEN_BREAKEVEN: {State.FINAL_EXIT_PENDING, State.CLOSED},
    State.FINAL_EXIT_PENDING: {State.CLOSED},
    State.CLOSED: set(),
    State.INVALIDATED: set(),
    State.RECON_BLOCKED: set(State),          # can be resolved back to any state by operator
    State.RISK_BLOCKED: set(State),
}

_BLOCKING = {State.RECON_BLOCKED, State.RISK_BLOCKED}
# Active (non-terminal) states can always be forced into a blocking state (fail-closed).
_ACTIVE = {
    State.IMPORTED, State.QUALIFIED, State.SETUP_WATCH, State.ENTRY_PENDING,
    State.OPEN_INITIAL_RISK, State.PARTIAL_PENDING, State.OPEN_BREAKEVEN, State.FINAL_EXIT_PENDING,
}


class TransitionError(RuntimeError):
    pass


@dataclass
class Transition:
    from_state: State
    to_state: State
    idempotency_key: str
    reason: str
    guard_results: dict = field(default_factory=dict)
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class Trade:
    trade_id: str
    state: State = State.IMPORTED
    transitions: list[Transition] = field(default_factory=list)
    _keys: set[str] = field(default_factory=set)

    def _legal(self, to: State) -> bool:
        if to in _BLOCKING and self.state in _ACTIVE:
            return True
        return to in _ALLOWED.get(self.state, set())

    def transition(
        self,
        to: State,
        idempotency_key: str,
        reason: str,
        guard: Callable[[], dict] | None = None,
    ) -> Transition:
        """Apply a guarded transition. Idempotent by ``idempotency_key``.

        If the key was already applied, returns the existing transition unchanged (no-op),
        which makes duplicate event delivery and restart replay safe.
        """
        if idempotency_key in self._keys:
            for t in self.transitions:
                if t.idempotency_key == idempotency_key:
                    return t

        if not self._legal(to):
            raise TransitionError(
                f"illegal transition {self.state.value} -> {to.value}"
            )

        guard_results = guard() if guard else {}
        if guard_results.get("_ok", True) is False:
            raise TransitionError(
                f"guard failed for {self.state.value} -> {to.value}: {guard_results}"
            )

        t = Transition(
            from_state=self.state,
            to_state=to,
            idempotency_key=idempotency_key,
            reason=reason,
            guard_results=guard_results,
        )
        self.transitions.append(t)
        self._keys.add(idempotency_key)
        self.state = to
        return t


def replay(trade_id: str, transitions: list[Transition]) -> Trade:
    """Rebuild a Trade's current state from a persisted, ordered transition log."""
    trade = Trade(trade_id=trade_id)
    for t in transitions:
        if t.idempotency_key in trade._keys:
            continue
        trade.transitions.append(t)
        trade._keys.add(t.idempotency_key)
        trade.state = t.to_state
    return trade
