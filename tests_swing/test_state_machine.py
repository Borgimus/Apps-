"""State machine: guards, idempotency (duplicate events), restart replay, fail-closed blocks."""
import pytest

from src.strategy.state_machine import (
    State,
    Trade,
    TransitionError,
    replay,
)


def test_happy_path():
    t = Trade("T1")
    t.transition(State.QUALIFIED, "k1", "qualified")
    t.transition(State.SETUP_WATCH, "k2", "setup")
    t.transition(State.ENTRY_PENDING, "k3", "breakout")
    t.transition(State.OPEN_INITIAL_RISK, "k4", "filled")
    assert t.state == State.OPEN_INITIAL_RISK
    assert len(t.transitions) == 4


def test_illegal_transition_rejected():
    t = Trade("T2")
    with pytest.raises(TransitionError):
        t.transition(State.CLOSED, "k1", "cannot close from IMPORTED")


def test_idempotent_duplicate_event_is_noop():
    t = Trade("T3")
    t.transition(State.QUALIFIED, "k1", "qualified")
    first = t.transitions[0]
    # Duplicate delivery of the same key -> same transition, no state churn.
    again = t.transition(State.QUALIFIED, "k1", "qualified-again")
    assert again is first
    assert len(t.transitions) == 1


def test_five_r_once_via_idempotency_key():
    t = Trade("T4")
    for to, k in [(State.QUALIFIED, "k1"), (State.SETUP_WATCH, "k2"),
                  (State.ENTRY_PENDING, "k3"), (State.OPEN_INITIAL_RISK, "k4")]:
        t.transition(to, k, "step")
    key = "T4:partial"
    t.transition(State.PARTIAL_PENDING, key, "5R")
    # A second 5R event with the same key does nothing even though state advanced.
    t.transition(State.OPEN_BREAKEVEN, "k5", "breakeven")
    dup = t.transition(State.PARTIAL_PENDING, key, "5R-dup")
    assert dup.to_state == State.PARTIAL_PENDING
    assert t.state == State.OPEN_BREAKEVEN  # unchanged by the duplicate


def test_restart_replay_reaches_same_state():
    t = Trade("T5")
    for to, k in [(State.QUALIFIED, "k1"), (State.SETUP_WATCH, "k2"),
                  (State.ENTRY_PENDING, "k3"), (State.OPEN_INITIAL_RISK, "k4")]:
        t.transition(to, k, "step")
    rebuilt = replay("T5", t.transitions)
    assert rebuilt.state == t.state == State.OPEN_INITIAL_RISK


def test_fail_closed_risk_block_from_open():
    t = Trade("T6")
    for to, k in [(State.QUALIFIED, "k1"), (State.SETUP_WATCH, "k2"),
                  (State.ENTRY_PENDING, "k3"), (State.OPEN_INITIAL_RISK, "k4")]:
        t.transition(to, k, "step")
    t.transition(State.RISK_BLOCKED, "block1", "missing stop")
    assert t.state == State.RISK_BLOCKED


def test_guard_failure_blocks_transition():
    t = Trade("T7")
    with pytest.raises(TransitionError):
        t.transition(State.QUALIFIED, "k1", "q", guard=lambda: {"_ok": False, "why": "stale"})
    assert t.state == State.IMPORTED
