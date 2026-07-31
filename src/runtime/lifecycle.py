"""Process lifecycle: startup reconciliation gate, readiness, and graceful shutdown.

Readiness is reported ONLY after startup reconciliation completes AND passes — a mismatch keeps
the process not-ready (so a load balancer / operator sees it as unavailable and no new risk is
taken). Graceful shutdown blocks new entries while CONTINUING to manage and close existing
positions; it never cancels protective stops or abandons positions.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Readiness:
    """Tracks whether startup reconciliation has completed and succeeded."""
    reconciliation_started: bool = False
    reconciliation_complete: bool = False
    reconciliation_ok: bool = False
    paper_endpoint_verified: bool = False
    reasons: list[str] = field(default_factory=list)

    def begin_reconciliation(self) -> None:
        self.reconciliation_started = True
        self.reconciliation_complete = False
        self.reconciliation_ok = False

    def complete_reconciliation(self, ok: bool, reasons: list[str] | None = None) -> None:
        self.reconciliation_complete = True
        self.reconciliation_ok = ok
        self.reasons = list(reasons or [])

    def is_ready(self) -> bool:
        """Ready only when the paper endpoint is verified and reconciliation completed cleanly."""
        return (self.paper_endpoint_verified
                and self.reconciliation_started
                and self.reconciliation_complete
                and self.reconciliation_ok)


@dataclass
class ShutdownController:
    """Graceful shutdown: drain new entries, keep managing/closing existing positions."""
    draining: bool = False
    preserve_open_orders: bool = True   # NEVER cancel protective stops on shutdown

    def request_shutdown(self) -> None:
        self.draining = True

    def accepting_new_entries(self) -> bool:
        return not self.draining

    def still_managing_positions(self) -> bool:
        # We always keep managing existing positions and their stops until they close/exit.
        return True

    def cancels_protective_stops(self) -> bool:
        return not self.preserve_open_orders  # always False by design
