# Known limitations & roadmap

## Known limitations (current state)

1. **Single-user, no auth** — local-first by design; do not expose publicly.
2. **SQLite + in-process engine** — great locally; a durable external queue and Postgres are needed for horizontal scale (seams are in place, see architecture doc).
3. **Orchestration modes are advisory** — `manager/pipeline/...` shape agent context and the demo runner drives an explicit pipeline, but there is no per-mode automatic scheduler (e.g. auto-starting the reviewer when a task hits `awaiting_review`; today you or an agent starts that run).
4. **No task-dependency scheduler** — dependencies are stored and displayed but don't auto-gate execution.
5. **Provider streaming is not surfaced token-by-token** — the UI is live per event (model call / tool call granularity), not per token.
6. **No real Git integration** — the versioned virtual file workspace covers files/diffs/restore; repo connection, branches, and PR workflows are roadmap items.
7. **File diffs are simplified** — common prefix/suffix line diff, not a full Myers diff.
8. **No shell/code-execution/web tools** — deliberately excluded from the MVP for safety; the tool registry is built to add them behind approval gates and a sandbox.
9. **Agent memory** exists at project level (pinned memory) and schema level for agents, but there is no automatic summarization/expiration job yet.
10. **Mock provider scripts** cover the demo roles and test markers; arbitrary objectives get a generic acknowledge-and-complete behavior.
11. **ESLint not configured** — strict `tsc --noEmit` is the lint gate in CI terms.
12. **Rerun editing** covers system prompt and temperature in the UI (the API accepts full message edits).

## Recommended next development priorities

1. **Reactive scheduler**: auto-start reviewer runs on `awaiting_review`, owner runs on `ready` with satisfied dependencies — this turns the stored orchestration modes into true automation.
2. **Postgres + BullMQ worker**: production durability and concurrency.
3. **Session auth + RBAC**, then multi-workspace.
4. **Sandboxed code-execution tool** (container-based) behind a high-risk approval gate — unlocks real software builds.
5. **Git integration**: repo connect, agent branches, diff review, user-approved push/merge.
6. **Token-level streaming** into the activity feed.
7. **Context engine v2**: embedding-based retrieval over messages/files/decisions, memory summarization + expiry.
8. **Debate/parallel mode judges**: first-class multi-proposal comparison UI.
9. **Notification channels**: email/Slack/Discord webhooks (the Notification model is channel-ready).
10. **Cost controls v2**: workspace daily budget enforcement (schema field exists), per-task caps, projections.
