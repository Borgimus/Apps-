# Security notes

## Model

Single-user, local-first. There is **no authentication layer**; isolation comes from running on localhost (or inside your own Docker network). Do not expose the port publicly as-is — session auth/RBAC is a roadmap item and the schema (Workspace root, actor fields everywhere) is designed for it.

## Protections in place

- **API keys never touch the database or frontend.** Model configurations store only the *name* of an environment variable; adapters read `process.env` server-side. `.env` is gitignored; `.env.example` contains placeholders only.
- **Per-agent tool allowlists** enforced in the single tool-execution path (`src/lib/tools/execute.ts`). A model requesting a tool outside its allowlist gets a recorded `denied` result — there is no alternate route to side effects.
- **Approval gates** for risky actions: file deletion (default-gated), file writes when `fileWriteRequiresApproval` is set, budget increases. Gated calls are persisted *before* execution and only execute after explicit human approval.
- **Input validation everywhere**: every API body and every tool input passes a zod schema.
- **Budget guardrails**: per-run cost caps stop runaway spending; iteration limits stop loops.
- **Immutable audit history**: model calls, tool calls, file versions, usage records and audit events are append-only.
- **Secret redaction helper** (`redactSecrets`) that strips common key formats (OpenAI/Anthropic-style, bearer tokens) — applied as defense-in-depth for text destined for display.
- **No arbitrary code execution**: the MVP tool set is deliberately limited to virtual file operations, task/message/decision operations, and approvals. There is no shell tool, no network tool.

## Prompt injection posture

Agents consume project files and messages that other agents (or you) authored. Mitigations: system prompts instruct agents to work only through logged tools; permissions are enforced *outside* the model; risky effects require human approval; everything is visible in the timeline. Residual risk: a malicious document could still influence an agent's text output and unprivileged tool use — treat agent output as untrusted, as the working rules do.

## Known gaps (honest list)

- No auth/RBAC/session management (single-user by design for the MVP)
- No rate limiting on the HTTP API
- No sandboxed code execution (no code execution at all — safe default)
- Secret redaction is pattern-based, not exhaustive
- SQLite file is unencrypted at rest
- No CSRF protection (relevant only if you expose the app beyond localhost)
