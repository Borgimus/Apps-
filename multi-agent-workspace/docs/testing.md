# Testing

```bash
npm test            # full suite (vitest)
npm run typecheck   # strict TypeScript
```

The suite runs against a **throwaway SQLite database** (`tests/tmp/test.db`) created fresh by the global setup — your dev database is untouched. Files run sequentially (one DB); each test builds isolated fixtures. All model interactions use the deterministic mock provider, so tests are offline and free.

## Coverage map (22 tests, 4 files)

| File | Covers |
|---|---|
| `tests/providers.test.ts` | Mock determinism, role scripts (reviewer verdict logic), retryable-error propagation, registry validation, retry/backoff behavior, cost math, Anthropic auth fail-fast |
| `tests/orchestrator.test.ts` | Run completion with full history (model calls, usage, audit events); file writes + versioning + attribution; permission denial + iteration-limit failure; graceful settling of text-only models; approval gate (approve executes & resumes, reject feeds back without executing); pause/resume; mid-flight cancellation without side effects; idempotency-key dedup; budget exhaustion → approval; provider-failure recording |
| `tests/review-flow.test.ts` | The full 8-run collaborative pipeline (plan → design → implement → changes-requested review → fix → approval → QA → report) with assertions on file versions, review messages, decisions, audit volume, and task/project completion; demo idempotency |
| `tests/api-flow.test.ts` | The primary user flow through real route handlers: health, create project/agent/task, start run, poll to completion, verify tool calls, versioned prompt rerun with preserved original, input-validation rejections, dashboard rollups |

## Writing new tests

Use `makeFixture()` / `addAgent()` from `tests/helpers.ts` for isolated workspace/project/agent setups, and `waitFor()` to await background run settlement. Prefer driving behavior through `startRun` (engine) or the exported route handlers (API) rather than poking the DB directly.
