#!/usr/bin/env python3
"""Apply and validate the approved $250 / 10-contract paper-only experiment."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "research/patches/paper_scaled_sizing_250_10.patch"


def run(*args: str) -> None:
    subprocess.run(args, cwd=ROOT, check=True)


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one match in {path}; found {count}: {old[:100]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def apply_existing_patch() -> None:
    run(sys.executable, "scripts/recount_patch_hunks.py", str(PATCH))
    run("git", "apply", "--check", str(PATCH))
    run("git", "apply", str(PATCH))


def strengthen_broker_paper_guard() -> None:
    path = ROOT / "scripts/session_runner.py"
    replace_once(
        path,
        '''            if not acct.is_paper:\n                raise RuntimeError(\n                    "paper_scaled_sizing_enabled requires a broker-confirmed paper account"\n                )\n''',
        '''            if not acct.is_paper:\n                logger.critical(\n                    "PAPER_SCALED_SIZING: broker reports a non-paper account — aborting session"\n                )\n                raise SystemExit(1)\n''',
    )


def separate_scaled_ledger() -> None:
    path = ROOT / "app/evaluation/post_session.py"
    text = path.read_text(encoding="utf-8")
    marker = "\n\nasync def _update_ledger("
    if text.count(marker) != 1:
        raise RuntimeError("Could not locate _update_ledger insertion point")

    helper = '''\n\ndef _ledger_file_for_settings(settings) -> str:\n    """Return a cohort-specific ledger path for scaled paper evaluation."""\n    ledger_file = getattr(settings, "evaluation_ledger_file", "./evaluation/ledger.json")\n    if not getattr(settings, "paper_scaled_sizing_enabled", False):\n        return ledger_file\n\n    ledger_path = Path(ledger_file)\n    budget = float(getattr(settings, "paper_scaled_premium_budget_dollars", 250.0))\n    cap = int(getattr(settings.universe, "max_contracts_per_position", 1))\n    budget_slug = f"{budget:g}".replace(".", "_")\n    return str(\n        ledger_path.with_name(\n            f"{ledger_path.stem}.paper_scaled_{budget_slug}_cap_{cap}{ledger_path.suffix}"\n        )\n    )\n'''
    path.write_text(text.replace(marker, helper + marker, 1), encoding="utf-8")

    replace_once(
        path,
        '        ledger_file = getattr(settings, "evaluation_ledger_file", "./evaluation/ledger.json")\n'
        '        ledger = EvaluationLedger.load(ledger_file)\n',
        '        ledger_file = _ledger_file_for_settings(settings)\n'
        '        if getattr(settings, "paper_scaled_sizing_enabled", False):\n'
        '            logger.info("Post-session: using separate scaled-sizing ledger %s", ledger_file)\n'
        '        ledger = EvaluationLedger.load(ledger_file)\n',
    )


def extend_tests() -> None:
    path = ROOT / "tests/test_paper_scaled_sizing.py"
    replace_once(
        path,
        'from app.evaluation.daily_report import DailyReport, to_markdown\n',
        'from app.evaluation.daily_report import DailyReport, to_markdown\n'
        'from app.evaluation.post_session import _ledger_file_for_settings\n',
    )
    replace_once(
        path,
        'with pytest.raises(ValidationError, match="cannot be used with live_trading_enabled"):',
        'with pytest.raises(ValidationError, match="live_trading_enabled"):',
    )

    ledger_tests = '''\n\ndef test_scaled_cohort_uses_separate_ledger_file():\n    settings = SimpleNamespace(\n        evaluation_ledger_file="./evaluation/ledger.json",\n        paper_scaled_sizing_enabled=True,\n        paper_scaled_premium_budget_dollars=250,\n        universe=SimpleNamespace(max_contracts_per_position=10),\n    )\n    assert _ledger_file_for_settings(settings).endswith(\n        "ledger.paper_scaled_250_cap_10.json"\n    )\n\n\ndef test_one_contract_cohort_keeps_original_ledger_file():\n    settings = SimpleNamespace(\n        evaluation_ledger_file="./evaluation/ledger.json",\n        paper_scaled_sizing_enabled=False,\n    )\n    assert _ledger_file_for_settings(settings) == "./evaluation/ledger.json"\n'''
    text = path.read_text(encoding="utf-8")
    marker = "\n\ndef test_markdown_labels_actual_and_normalized_pnl():"
    if text.count(marker) != 1:
        raise RuntimeError("Could not locate markdown test insertion point")
    path.write_text(text.replace(marker, ledger_tests + marker, 1), encoding="utf-8")


def remove_temporary_files() -> None:
    for relative in (
        "research/patches/paper_scaled_sizing_250_10.patch",
        "scripts/recount_patch_hunks.py",
        "scripts/apply_paper_scaled_sizing_repo_update.py",
    ):
        path = ROOT / relative
        if path.exists():
            path.unlink()


def main() -> None:
    apply_existing_patch()
    strengthen_broker_paper_guard()
    separate_scaled_ledger()
    extend_tests()
    remove_temporary_files()
    print("paper-scaled-sizing repository update applied")


if __name__ == "__main__":
    main()
