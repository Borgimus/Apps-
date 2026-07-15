# Data model

Defined in `prisma/schema.prisma`. JSON-shaped columns are `String` (SQLite-safe) parsed/validated at the data-access layer with zod.

## Immutability rules

These tables are **append-only** — rows are never updated or deleted:

- `ModelCall` — reruns create new rows chained via `parentCallId` with an incremented `version`
- `ToolCall`
- `FileVersion` — edits and restores always create a new version
- `UsageRecord`
- `AuditEvent`

## Entities

| Entity | Purpose | Notes |
|---|---|---|
| `Workspace` | Single-user tenant root; global instructions, daily budget | Single row in local mode |
| `Project` | Objective, instructions, status, orchestration mode, budget | Owns tasks, files, messages, decisions, approvals, memory, events |
| `ModelConfig` | Provider + model ID + endpoint + pricing + sampling defaults | **Separate from agents**; `apiKeyEnvVar` stores the env-var *name*, never a key |
| `AgentTemplate` | Reusable role presets (11 seeded) | |
| `Agent` | Name, role, system prompt, model reference, tool allowlist, permissions, per-run budget, status, memory | Switch `modelConfigId` to change models |
| `ProjectAgent` | Many-to-many assignment | |
| `Task` | Title, description, acceptance criteria, status, priority, owner, reviewer, creator, result, cost | Subtasks via `parentTaskId` |
| `TaskDependency` | DAG edges between tasks | |
| `AgentRun` | One working session: objective, durable transcript, iterations, tokens, cost, status, error, idempotency key, pending gated tool | The engine's unit of execution |
| `ModelCall` | Full immutable record of one provider request/response incl. system prompt, messages, tool defs, settings, **context manifest**, usage, cost, duration | Powers the Prompt Inspector |
| `ToolCall` | Tool name, validated input, output, status (`ok/error/denied/rejected`), risk, duration | |
| `Message` | Typed structured agent/user communication (13 types) | Linked to project/task/run |
| `ProjectFile` / `FileVersion` | Virtual project file workspace with full version history and author attribution | Optimistic concurrency via `baseVersion` |
| `Decision` | Decision log entries | |
| `ApprovalRequest` | Action, reason, payload, risk, status, resolution | Blocks the owning run while pending |
| `UsageRecord` | Per-model-call token/cost accounting | Rolled up by project/agent/model/day |
| `Notification` | In-app notification feed | |
| `AuditEvent` | The activity timeline: actor, type, summary, data | Indexed by `(projectId, createdAt)` |
| `ProjectMemory` | Keyed shared context; `pinned` entries are injected into every agent prompt | Supports expiry timestamps |

## Task status flow

`backlog → ready → in_progress → (blocked | awaiting_review | awaiting_approval) → completed | failed | cancelled`

- Owner completes a task that has a reviewer → `awaiting_review`
- Reviewer's run summary containing "changes requested" → back to `in_progress`; otherwise → `completed`
