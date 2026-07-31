# Delegation Log

The supervising agent (project supervisor) remains accountable for the entire project. It must
delegate bounded, low-risk work to the least-expensive capable model when safe, and personally retain
architecture ownership, trading-rule interpretation, risk controls, broker execution, reconciliation
design, security decisions, and final integration.

Every delegated result is a **proposal** until the supervisor inspects the full artifact, checks it
against the task contract, independently runs the required tests, reviews security/trading-risk/
data-integrity/integration effects, and records APPROVED / APPROVED_WITH_CHANGES / REJECTED. Approval
can never be delegated back to the model that did the work.

## Environment constraint for Phase 0–1

In this execution environment, spawning cold subagents re-derives the full project context on each
call (high token cost) and the harness discourages unsolicited subagent use. Per the build prompt's
explicit fallback — *"If subagents or alternate models are unavailable, the supervising agent must
record that limitation and complete the work directly without lowering the review standard."* — the
supervisor completed Phase 0–1 **directly**, retaining every review step (formula derivation, test
authorship, independent test execution, safety review). No delegated task in these phases bypassed
review because none was delegated.

Delegation is planned for Phase 2+ where bounded, non-write, non-credentialed tasks appear (e.g.
docstring drafts, test-case enumeration, lint classification, log/backtest summarization). Each such
task will get a written contract and a row below.

## Task contract template

```
Task ID:            <id>
Objective:          <what>
Model/agent class:  <least-cost capable>
May inspect/change: <exact files/modules>
Context/interfaces: <links>
Output format:      <format>
Acceptance criteria:<criteria>
Validation cmds:    <tests>
Prohibited:         commit/push/merge/deploy; secrets; broker connect; order submit/cancel/replace/
                    approve; changing rules/risk/endpoints/deploy; self-approval; scope expansion
Token/effort budget:<budget>
```

## Records

| Task ID | Model/class | Reason | Scope (files) | Validation | Decision | Reason | Commit/PR |
|---------|-------------|--------|---------------|------------|----------|--------|-----------|
| P0-P1-direct | supervisor (self) | subagents cost-inefficient here; fallback clause invoked | all Phase 0–1 files | `pytest -c pytest_swing.ini` run by supervisor | N/A (not delegated) | completed directly, full review standard retained | (this branch) |
| P2-direct | supervisor (self) | fallback clause invoked; safety-critical (broker faults, reconciliation, session gating) kept under supervisor | `src/data/{calendar,market_data,corporate_actions}.py`, `src/broker/retry.py`, `src/execution/reconciliation.py`, adapter retry wiring, 5 test files | `pytest -c pytest_swing.ini` (126 passed) + independent calendar spot-checks (Easter/observance/next-open) run by supervisor | N/A (not delegated) | completed directly; calendar verified against real 2026 dates | (this branch) |
| P3-direct | supervisor (self) | fallback clause invoked; security-sensitive (auth, secret redaction, schema/idempotency) kept under supervisor | `src/storage/*`, `src/api/*`, `src/notifications/*`, `migrations/*`, 7 test files | `pytest -c pytest_swing.ini` (149 passed/1 skipped lean; 151 with SQLAlchemy) + ORM models validated with the dep installed by supervisor | N/A (not delegated) | completed directly; audit-reconstruction and secret-redaction verified | (this branch) |
| P4-direct | supervisor (self) | fallback clause invoked; trading-rule interpretation (lookahead, exits, AI boundary) kept under supervisor | `src/backtest/*`, `src/ai_review/*`, 5 test files | `pytest -c pytest_swing.ini` (180 passed/1 skipped lean; 181 with SQLAlchemy) + engine no-lookahead/gap/5R-once/repro scenarios authored & run by supervisor | N/A (not delegated) | completed directly; anti-lookahead and AI advisory-only boundaries verified | (this branch) |
| P5-direct | supervisor (self) | fallback clause invoked; security/deploy decisions (secret redaction, fail-closed gate, endpoint verify, shutdown) kept under supervisor | `src/runtime/*`, `deploy/*`, `companion/*`, `scripts/{run_swing.py,backup_db.sh,restore_db.sh}`, docs, 4 test files | `pytest -c pytest_swing.ini` (201 passed/1 skipped lean; 202 with SQLAlchemy) + entrypoint import + shell `bash -n` run by supervisor | N/A (not delegated) | completed directly; readiness-blocks-on-mismatch and companion-has-no-order-authority verified | (this branch) |
