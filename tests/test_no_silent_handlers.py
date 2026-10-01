"""Ratchet: broad exception handlers that hide failures may not increase.

Six trading days were lost to a 401 caught by ``except Exception`` and
discarded. A broad handler is acceptable only if it raises, logs at INFO or
above, records the failure, or carries the exception into its result. Each
file's count of handlers that do none of these may only go down; lower the
baseline below when you remove one.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VISIBLE_CALLS = {"warning", "error", "critical", "exception", "info", "notify",
                 "add_failure", "_section_failed"}

BASELINE = {
    "app/api/dashboard_api.py": 7,
    "app/api/models.py": 1,
    "app/backtesting/backtest_engine.py": 1,
    "app/brokers/alpaca_broker.py": 1,
    "app/data/yfinance_data.py": 2,
    "app/evaluation/daily_report.py": 1,
    "app/evaluation/market_data_observer.py": 1,
    "app/evaluation/session_context.py": 1,
    "app/scanning/yfinance_scanner.py": 3,
    "app/strategies/liquidity_filter.py": 1,
    "app/strategies/opening_range_breakout.py": 1,
    "app/strategies/signal_quality.py": 2,
    "app/utils/logging_setup.py": 1,
    "scripts/capture_session_fingerprint.py": 3,
    "scripts/eval_smoke_test.py": 3,
    "scripts/fill_lifecycle_test.py": 1,
    "scripts/session_runner.py": 2,
    "scripts/validate_non_orb.py": 1,
}


def _broad(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(getattr(t, "id", getattr(t, "attr", "")) in ("Exception", "BaseException")
               for t in types)


def _call_name(call: ast.Call) -> str:
    return getattr(call.func, "attr", getattr(call.func, "id", ""))


def _visible(handler: ast.ExceptHandler) -> bool:
    in_debug = {id(node) for call in ast.walk(handler)
                if isinstance(call, ast.Call) and _call_name(call) == "debug"
                for node in ast.walk(call)}
    for node in ast.walk(handler):
        if isinstance(node, ast.Raise):
            return True
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name in VISIBLE_CALLS or name.startswith(("_record", "record_")):
                return True
        if (handler.name and isinstance(node, ast.Name) and node.id == handler.name
                and id(node) not in in_debug):
            return True
    return False


def silent_handlers() -> dict:
    counts = {}
    for root in ("app", "scripts"):
        for path in sorted((ROOT / root).rglob("*.py")):
            tree = ast.parse(path.read_text())
            n = sum(1 for node in ast.walk(tree)
                    if isinstance(node, ast.ExceptHandler) and _broad(node) and not _visible(node))
            if n:
                counts[str(path.relative_to(ROOT))] = n
    return counts


def test_silent_exception_handlers_do_not_increase():
    grew = {f: (BASELINE.get(f, 0), n) for f, n in silent_handlers().items()
            if n > BASELINE.get(f, 0)}
    assert not grew, (
        "New broad exception handlers swallow failures without logging, raising or "
        f"recording them (file: (baseline, now)): {grew}"
    )


def test_ratchet_detects_a_swallowed_failure():
    swallowed = ast.parse("try:\n    f()\nexcept Exception:\n    pass\n").body[0].handlers[0]
    logged = ast.parse("try:\n    f()\nexcept Exception as e:\n    log.warning('x %s', e)\n").body[0].handlers[0]
    debug_only = ast.parse("try:\n    f()\nexcept Exception as e:\n    log.debug('x %s', e)\n").body[0].handlers[0]
    assert not _visible(swallowed) and _visible(logged) and not _visible(debug_only)


def test_failed_report_section_reads_as_unavailable_not_zero():
    from app.evaluation.daily_report import DailyReport, _generate_notes, _section_failed, to_markdown

    report = DailyReport(date="2026-09-30", session_start=None, session_end=None)
    _section_failed(report, "scan_pipeline", RuntimeError("no such table"))
    report.data_fetch_rejections = 4
    notes, _ = _generate_notes(report)
    assert any("scan_pipeline" in n for n in notes)
    assert any("4 scan candidate" in n for n in notes)
    assert "| Unavailable report sections | scan_pipeline |" in to_markdown(report)
