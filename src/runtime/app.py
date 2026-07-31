"""Process assembly: verify paper endpoint, run startup reconciliation, gate readiness.

This wires the runtime pieces into a startable service without binding forever, so the startup
sequence (which must complete BEFORE readiness is reported) is unit-testable. Broker state wins
during startup reconciliation; any unresolved discrepancy keeps the process not-ready.
"""
from __future__ import annotations

from typing import Callable

from src.broker.alpaca_paper import LiveEndpointRejected, assert_paper_endpoint
from src.execution.reconciliation import ReconResult
from src.runtime.lifecycle import Readiness, ShutdownController


def verify_paper_endpoint(readiness: Readiness, base_url: str) -> None:
    """Fail-closed paper-endpoint check. Raises (terminating startup) on a non-paper endpoint."""
    assert_paper_endpoint(base_url)  # raises LiveEndpointRejected on anything but the paper host
    readiness.paper_endpoint_verified = True


def run_startup_reconciliation(readiness: Readiness,
                               reconcile_fn: Callable[[], ReconResult]) -> ReconResult:
    """Run reconciliation at startup and gate readiness on the result.

    Readiness becomes True only if the reconciliation reports no blocking incidents.
    """
    readiness.begin_reconciliation()
    result = reconcile_fn()
    reasons = [f"{i.kind.value}:{i.symbol}" for i in result.incidents]
    readiness.complete_reconciliation(ok=not result.blocks_new_risk, reasons=reasons)
    return result


def build_ready_provider(readiness: Readiness) -> Callable[[], bool]:
    return readiness.is_ready


class SwingService:
    """Assembled service handle (startup only; the caller owns the serving loop)."""

    def __init__(self, *, base_url: str):
        self.readiness = Readiness()
        self.shutdown = ShutdownController()
        self._base_url = base_url

    def start(self, reconcile_fn: Callable[[], ReconResult]) -> ReconResult:
        verify_paper_endpoint(self.readiness, self._base_url)  # may raise -> terminate safely
        return run_startup_reconciliation(self.readiness, reconcile_fn)

    def ready(self) -> bool:
        return self.readiness.is_ready() and not self.shutdown.draining


__all__ = [
    "SwingService",
    "verify_paper_endpoint",
    "run_startup_reconciliation",
    "build_ready_provider",
    "LiveEndpointRejected",
]
