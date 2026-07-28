#!/usr/bin/env python3
"""Apply the approved $250 / 10-contract paper-only sizing experiment."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected exactly one match in {path}, found {count}: {old[:80]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def insert_before_once(path: Path, marker: str, insertion: str, *, start_marker: str | None = None) -> None:
    text = path.read_text(encoding="utf-8")
    start = text.index(start_marker) if start_marker else 0
    index = text.index(marker, start)
    path.write_text(text[:index] + insertion + text[index:], encoding="utf-8")


def update_settings() -> None:
    path = ROOT / "app/config/settings.py"
    replace_once(
        path,
        "    fill_test_max_spread_pct: float = 0.20 # abort contract if spread/mid > this\n",
        "    fill_test_max_spread_pct: float = 0.20 # abort contract if spread/mid > this\n"
        "\n"
        "    # Paper-only scaled-sizing experiment. Disabled by default.\n"
        "    # Quantity is bounded by the ask-price premium budget and universe cap.\n"
        "    paper_scaled_sizing_enabled: bool = False\n"
        "    paper_scaled_premium_budget_dollars: float = 250.0\n",
    )
    guard = (
        "        if self.paper_scaled_sizing_enabled:\n"
        "            if self.live_trading_enabled:\n"
        "                raise ValueError(\n"
        "                    \"paper_scaled_sizing_enabled cannot be used with live_trading_enabled=true.\"\n"
        "                )\n"
        "            if not self.paper_evaluation_mode:\n"
        "                raise ValueError(\n"
        "                    \"paper_scaled_sizing_enabled requires paper_evaluation_mode=true.\"\n"
        "                )\n"
        "            if self.realistic_fill_test_mode:\n"
        "                raise ValueError(\n"
        "                    \"paper_scaled_sizing_enabled is incompatible with realistic_fill_test_mode.\"\n"
        "                )\n"
        "            if self.paper_scaled_premium_budget_dollars <= 0:\n"
        "                raise ValueError(\n"
        "                    \"paper_scaled_premium_budget_dollars must be greater than zero.\"\n"
        "                )\n"
        "            if self.universe.max_contracts_per_position < 1:\n"
        "                raise ValueError(\"max_contracts_per_position must be at least one.\")\n"
    )
    insert_before_once(
        path,
        "        return self\n",
        guard,
        start_marker='    @model_validator(mode="after")\n    def guard_eval_mode(self):\n',
    )


def write_sizing_helper() -> None:
    path = ROOT / "app/risk/paper_sizing.py"
    path.write_text(
        '''"""Pure helpers for the controlled paper-only scaled-sizing experiment."""\n\n'
        'from __future__ import annotations\n\n'
        'from decimal import Decimal, InvalidOperation\n'
        'from typing import Union\n\n\n'
        'Number = Union[Decimal, float, int, str]\n\n\n'
        'def calculate_paper_scaled_quantity(\n'
        '    option_ask: Number,\n'
        '    premium_budget_dollars: Number,\n'
        '    max_contracts: int,\n'
        ') -> int:\n'
        '    """Return floor(budget / (ask * 100)), capped by max_contracts.\n\n'
        '    Zero means one contract exceeds the approved premium budget and the\n'
        '    entry must be skipped. Invalid or non-positive inputs fail closed.\n'
        '    """\n'
        '    try:\n'
        '        ask = Decimal(str(option_ask))\n'
        '        budget = Decimal(str(premium_budget_dollars))\n'
        '        cap = int(max_contracts)\n'
        '    except (InvalidOperation, TypeError, ValueError):\n'
        '        return 0\n\n'
        '    if ask <= 0 or budget <= 0 or cap <= 0:\n'
        '        return 0\n\n'
        '    contract_cost = ask * Decimal("100")\n'
        '    budget_quantity = int(budget // contract_cost)\n'
        '    return min(cap, budget_quantity)\n\n\n'
        'def normalized_one_contract_pnl(realized_pnl: Number, quantity: int) -> float:\n'
        '    """Normalize a multi-contract trade P&L result to one contract."""\n'
        '    qty = max(1, int(quantity or 1))\n'
        '    return float(Decimal(str(realized_pnl)) / Decimal(qty))\n''',
        encoding="utf-8",
    )


def update_session_runner() -> None:
    path = ROOT / "scripts/session_runner.py"
    old = '''        request = OrderRequest(\n            symbol=symbol,\n            option_symbol=contract.option_symbol,\n            side=OrderSide.BUY_TO_OPEN,\n            quantity=1,\n            order_type=OrderType.LIMIT,\n            limit_price=limit_price,\n            strategy_id=sig.strategy_id,\n            notes=sig.notes,\n        )\n'''
    new = '''        request_quantity = 1\n        if getattr(settings, "paper_scaled_sizing_enabled", False):\n            if not acct.is_paper:\n                logger.critical(\n                    "PAPER_SCALED_SIZING: broker reports a non-paper account — aborting session"\n                )\n                raise SystemExit(1)\n\n            from app.risk.paper_sizing import calculate_paper_scaled_quantity\n\n            premium_budget = getattr(\n                settings, "paper_scaled_premium_budget_dollars", 250.0\n            )\n            max_contracts = getattr(\n                settings.universe, "max_contracts_per_position", 1\n            )\n            request_quantity = calculate_paper_scaled_quantity(\n                option_ask=contract.ask,\n                premium_budget_dollars=premium_budget,\n                max_contracts=max_contracts,\n            )\n\n            if request_quantity < 1:\n                reason = "paper_scaled_budget_below_one_contract"\n                logger.info(\n                    "Paper scaled sizing blocked | %s | ask=%.4f | budget=$%.2f | cap=%d",\n                    contract.option_symbol,\n                    float(contract.ask),\n                    float(premium_budget),\n                    int(max_contracts),\n                )\n                if _bridge is not None:\n                    _bridge.final_decision = "blocked"\n                    _bridge.exact_block_reason = reason\n                await _shadow_blocked(\n                    reason,\n                    _osym=contract.option_symbol,\n                    _lp=float(limit_price),\n                    _ask=float(contract.ask),\n                )\n                if journal:\n                    await journal.record_rejection(\n                        strategy_id=sig.strategy_id,\n                        signal_direction=sig.direction.value,\n                        underlying_symbol=symbol,\n                        underlying_price=sig.price,\n                        option_symbol=contract.option_symbol,\n                        rejection_reason=reason,\n                        entry_time=now,\n                    )\n                    await journal.commit()\n                continue\n\n            logger.info(\n                "Paper scaled sizing | %s | ask=%.4f | budget=$%.2f "\n                "| qty=%d | premium=$%.2f | cap=%d",\n                contract.option_symbol,\n                float(contract.ask),\n                float(premium_budget),\n                request_quantity,\n                float(contract.ask) * 100 * request_quantity,\n                int(max_contracts),\n            )\n\n        request = OrderRequest(\n            symbol=symbol,\n            option_symbol=contract.option_symbol,\n            side=OrderSide.BUY_TO_OPEN,\n            quantity=request_quantity,\n            order_type=OrderType.LIMIT,\n            limit_price=limit_price,\n            strategy_id=sig.strategy_id,\n            notes=sig.notes,\n        )\n'''
    replace_once(path, old, new)


def update_daily_report() -> None:
    path = ROOT / "app/evaluation/daily_report.py"
    replace_once(
        path,
        "    realized_pnl: float = 0.0\n    unrealized_pnl: float = 0.0\n",
        "    realized_pnl: float = 0.0\n"
        "    one_contract_normalized_pnl: float = 0.0\n"
        "    unrealized_pnl: float = 0.0\n"
        "    contracts_filled: int = 0\n"
        "    sizing_cohort: str = \"one_contract\"\n"
        "    premium_budget_dollars: Optional[float] = None\n"
        "    contract_cap: int = 1\n",
    )
    replace_once(
        path,
        "    from app.evaluation.ledger import PHASE3_START\n"
        "    _phase = \"phase3\" if session_date >= PHASE3_START else \"pre_phase3\"\n"
        "    _evidence_type = \"clean_evaluation\" if _phase == \"phase3\" else \"engineering_evidence_only\"\n",
        "    from app.evaluation.ledger import PHASE3_START\n"
        "    _scaled = bool(settings and getattr(settings, \"paper_scaled_sizing_enabled\", False))\n"
        "    _budget = (\n"
        "        float(getattr(settings, \"paper_scaled_premium_budget_dollars\", 250.0))\n"
        "        if _scaled else None\n"
        "    )\n"
        "    _cap = (\n"
        "        int(getattr(settings.universe, \"max_contracts_per_position\", 1))\n"
        "        if settings else 1\n"
        "    )\n"
        "    _cohort = (\n"
        "        f\"paper_scaled_budget_{_budget:g}_cap_{_cap}\" if _scaled else \"one_contract\"\n"
        "    )\n"
        "    _phase = \"phase3\" if session_date >= PHASE3_START else \"pre_phase3\"\n"
        "    _evidence_type = (\n"
        "        \"scaled_paper_evaluation\" if _scaled\n"
        "        else (\"clean_evaluation\" if _phase == \"phase3\" else \"engineering_evidence_only\")\n"
        "    )\n",
    )
    replace_once(
        path,
        "        phase=_phase,\n        evidence_type=_evidence_type,\n    )\n",
        "        phase=_phase,\n"
        "        evidence_type=_evidence_type,\n"
        "        sizing_cohort=_cohort,\n"
        "        premium_budget_dollars=_budget,\n"
        "        contract_cap=_cap,\n"
        "    )\n",
    )
    replace_once(
        path,
        "    pnls = [float(t.realized_pnl) for t in closed]\n"
        "    report.realized_pnl = sum(pnls)\n",
        "    pnls = [float(t.realized_pnl) for t in closed]\n"
        "    quantities = [\n"
        "        max(1, int(getattr(t, \"filled_quantity\", None) or getattr(t, \"quantity\", 1) or 1))\n"
        "        for t in closed\n"
        "    ]\n"
        "    from app.risk.paper_sizing import normalized_one_contract_pnl\n"
        "    report.realized_pnl = sum(pnls)\n"
        "    report.contracts_filled = sum(quantities)\n"
        "    report.one_contract_normalized_pnl = sum(\n"
        "        normalized_one_contract_pnl(pnl, qty)\n"
        "        for pnl, qty in zip(pnls, quantities)\n"
        "    )\n",
    )
    replace_once(
        path,
        "    total_attempted = r.trades_submitted + r.trades_rejected\n\n",
        "    total_attempted = r.trades_submitted + r.trades_rejected\n\n"
        "    if r.sizing_cohort != \"one_contract\":\n"
        "        notes.append(\n"
        "            f\"Separate paper-only scaled-sizing cohort: {r.sizing_cohort}; \"\n"
        "            \"do not merge with the frozen one-contract clean cohort\"\n"
        "        )\n\n",
    )
    insert_before_once(
        path,
        '    return f"""# Daily Evaluation Report — {r.date}\n',
        "    if r.sizing_cohort != \"one_contract\":\n"
        "        phase_banner += (\n"
        "            \"\\n\\n> **Separate paper-only scaled-sizing cohort** — actual P&L reflects \"\n"
        "            \"multi-contract sizing. One-contract-normalized P&L is shown for comparison; \"\n"
        "            \"do not merge this session into the frozen one-contract clean cohort.\"\n"
        "        )\n\n"
        "    budget_str = (\n"
        "        f\"${r.premium_budget_dollars:.2f}\"\n"
        "        if r.premium_budget_dollars is not None else \"n/a\"\n"
        "    )\n\n",
    )
    replace_once(
        path,
        "| Realized PnL | ${r.realized_pnl:.2f} |\n"
        "| Unrealized PnL | ${r.unrealized_pnl:.2f} |\n",
        "| Actual realized PnL | ${r.realized_pnl:.2f} |\n"
        "| One-contract-normalized PnL | ${r.one_contract_normalized_pnl:.2f} |\n"
        "| Unrealized PnL | ${r.unrealized_pnl:.2f} |\n"
        "| Contracts filled | {r.contracts_filled} |\n"
        "| Sizing cohort | {r.sizing_cohort} |\n"
        "| Premium budget | {budget_str} |\n"
        "| Contract cap | {r.contract_cap} |\n",
    )
    replace_once(
        path,
        '                f"pnl=${report.realized_pnl:.2f} | "\n'
        '                f"win={win_rate_str} | "\n',
        '                f"pnl=${report.realized_pnl:.2f} | "\n'
        '                f"normalized=${report.one_contract_normalized_pnl:.2f} | "\n'
        '                f"win={win_rate_str} | "\n',
    )
    replace_once(
        path,
        '                "realized_pnl": report.realized_pnl,\n'
        '                "win_rate": report.win_rate,\n',
        '                "realized_pnl": report.realized_pnl,\n'
        '                "one_contract_normalized_pnl": report.one_contract_normalized_pnl,\n'
        '                "sizing_cohort": report.sizing_cohort,\n'
        '                "win_rate": report.win_rate,\n',
    )


def update_post_session() -> None:
    path = ROOT / "app/evaluation/post_session.py"
    helper = '''\n\ndef _ledger_file_for_settings(settings) -> str:\n    """Return a cohort-specific ledger path for scaled paper evaluation."""\n    ledger_file = getattr(settings, "evaluation_ledger_file", "./evaluation/ledger.json")\n    if not getattr(settings, "paper_scaled_sizing_enabled", False):\n        return ledger_file\n\n    ledger_path = Path(ledger_file)\n    budget = float(getattr(settings, "paper_scaled_premium_budget_dollars", 250.0))\n    cap = int(getattr(settings.universe, "max_contracts_per_position", 1))\n    budget_slug = f"{budget:g}".replace(".", "_")\n    return str(\n        ledger_path.with_name(\n            f"{ledger_path.stem}.paper_scaled_{budget_slug}_cap_{cap}{ledger_path.suffix}"\n        )\n    )\n'''
    insert_before_once(path, "\n\nasync def _update_ledger(", helper)
    replace_once(
        path,
        '        ledger_file = getattr(settings, "evaluation_ledger_file", "./evaluation/ledger.json")\n'
        '        ledger = EvaluationLedger.load(ledger_file)\n',
        '        ledger_file = _ledger_file_for_settings(settings)\n'
        '        if getattr(settings, "paper_scaled_sizing_enabled", False):\n'
        '            logger.info("Post-session: using separate scaled-sizing ledger %s", ledger_file)\n'
        '        ledger = EvaluationLedger.load(ledger_file)\n',
    )


def write_tests() -> None:
    path = ROOT / "tests/test_paper_scaled_sizing.py"
    path.write_text(
        '''from types import SimpleNamespace\n\n'
        'import pytest\n'
        'from pydantic import ValidationError\n\n'
        'from app.config.settings import Settings\n'
        'from app.evaluation.daily_report import DailyReport, to_markdown\n'
        'from app.evaluation.post_session import _ledger_file_for_settings\n'
        'from app.risk.paper_sizing import (\n'
        '    calculate_paper_scaled_quantity,\n'
        '    normalized_one_contract_pnl,\n'
        ')\n\n\n'
        '@pytest.mark.parametrize(\n'
        '    ("ask", "expected"),\n'
        '    [\n'
        '        ("0.10", 10),\n'
        '        ("0.20", 10),\n'
        '        ("0.50", 5),\n'
        '        ("2.50", 1),\n'
        '        ("2.51", 0),\n'
        '        ("0", 0),\n'
        '    ],\n'
        ')\n'
        'def test_calculate_paper_scaled_quantity(ask, expected):\n'
        '    assert calculate_paper_scaled_quantity(ask, 250, 10) == expected\n\n\n'
        'def test_quantity_respects_lower_contract_cap():\n'
        '    assert calculate_paper_scaled_quantity("0.10", 250, 4) == 4\n\n\n'
        'def test_normalized_one_contract_pnl():\n'
        '    assert normalized_one_contract_pnl(40, 10) == 4.0\n'
        '    assert normalized_one_contract_pnl(-15, 5) == -3.0\n\n\n'
        'def test_scaled_sizing_requires_paper_evaluation_mode():\n'
        '    with pytest.raises(ValidationError, match="requires paper_evaluation_mode"):\n'
        '        Settings(\n'
        '            paper_scaled_sizing_enabled=True,\n'
        '            paper_evaluation_mode=False,\n'
        '            live_trading_enabled=False,\n'
        '        )\n\n\n'
        'def test_scaled_sizing_rejects_live_mode():\n'
        '    with pytest.raises(ValidationError, match="live_trading_enabled"):\n'
        '        Settings(\n'
        '            paper_scaled_sizing_enabled=True,\n'
        '            paper_evaluation_mode=True,\n'
        '            live_trading_enabled=True,\n'
        '        )\n\n\n'
        'def test_scaled_sizing_rejects_fill_test_mode():\n'
        '    with pytest.raises(ValidationError, match="incompatible with realistic_fill_test_mode"):\n'
        '        Settings(\n'
        '            paper_scaled_sizing_enabled=True,\n'
        '            paper_evaluation_mode=True,\n'
        '            live_trading_enabled=False,\n'
        '            realistic_fill_test_mode=True,\n'
        '        )\n\n\n'
        'def test_scaled_sizing_accepts_guarded_paper_mode():\n'
        '    settings = Settings(\n'
        '        paper_scaled_sizing_enabled=True,\n'
        '        paper_evaluation_mode=True,\n'
        '        live_trading_enabled=False,\n'
        '        realistic_fill_test_mode=False,\n'
        '        paper_scaled_premium_budget_dollars=250,\n'
        '    )\n'
        '    assert settings.paper_scaled_sizing_enabled is True\n\n\n'
        'def test_scaled_cohort_uses_separate_ledger_file():\n'
        '    settings = SimpleNamespace(\n'
        '        evaluation_ledger_file="./evaluation/ledger.json",\n'
        '        paper_scaled_sizing_enabled=True,\n'
        '        paper_scaled_premium_budget_dollars=250,\n'
        '        universe=SimpleNamespace(max_contracts_per_position=10),\n'
        '    )\n'
        '    assert _ledger_file_for_settings(settings).endswith(\n'
        '        "ledger.paper_scaled_250_cap_10.json"\n'
        '    )\n\n\n'
        'def test_one_contract_cohort_keeps_original_ledger_file():\n'
        '    settings = SimpleNamespace(\n'
        '        evaluation_ledger_file="./evaluation/ledger.json",\n'
        '        paper_scaled_sizing_enabled=False,\n'
        '    )\n'
        '    assert _ledger_file_for_settings(settings) == "./evaluation/ledger.json"\n\n\n'
        'def test_markdown_labels_actual_and_normalized_pnl():\n'
        '    report = DailyReport(\n'
        '        date="2026-07-29",\n'
        '        session_start=None,\n'
        '        session_end=None,\n'
        '        realized_pnl=40,\n'
        '        one_contract_normalized_pnl=4,\n'
        '        contracts_filled=10,\n'
        '        sizing_cohort="paper_scaled_budget_250_cap_10",\n'
        '        premium_budget_dollars=250,\n'
        '        contract_cap=10,\n'
        '        phase="phase3",\n'
        '        evidence_type="scaled_paper_evaluation",\n'
        '    )\n'
        '    markdown = to_markdown(report)\n'
        '    assert "Actual realized PnL | $40.00" in markdown\n'
        '    assert "One-contract-normalized PnL | $4.00" in markdown\n'
        '    assert "Separate paper-only scaled-sizing cohort" in markdown\n''',
        encoding="utf-8",
    )


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
    update_settings()
    write_sizing_helper()
    update_session_runner()
    update_daily_report()
    update_post_session()
    write_tests()
    remove_temporary_files()
    print("paper-scaled-sizing repository update applied")


if __name__ == "__main__":
    main()
