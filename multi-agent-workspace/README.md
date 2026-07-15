# Multi-Agent Workspace

A model-agnostic project workspace where multiple AI agents — Claude, OpenAI-compatible models, local models, or a free deterministic mock — collaborate on shared projects under **full human visibility and control**.

Agents plan, delegate, implement, review each other's work, and produce shared artifacts. Every prompt, response, tool call, task change, file edit, decision, and approval is recorded in an immutable, searchable history and streamed live to the UI.

![Concept](docs/architecture.md) — see `docs/` for the full documentation set.

## What it does

- **Projects** with objectives, instructions, shared context, tasks, files, decisions and budgets
- **Agents** with a role, system prompt, model, tool allowlist, permissions and per-run budget — created from 11 templates (PM, Developer, Architect, Reviewer, Security Reviewer, QA, Designer, Docs Writer, Researcher, Data Analyst, Devil's Advocate)
- **Model-agnostic**: agents can be switched between Anthropic, any OpenAI-compatible endpoint (OpenAI, OpenRouter, Ollama, vLLM…), or the mock provider — without rebuilding anything
- **Orchestration engine** with durable runs: pause, resume, cancel, retries with backoff, iteration limits, budget stops, idempotency keys, restart recovery
- **Human approval gates**: gated tools (file writes when configured, deletions, budget increases) pause the agent until you approve, reject (with feedback the agent sees), or annotate
- **Live activity timeline** over Server-Sent Events, with expandable events
- **Prompt Inspector**: the exact system prompt, messages, tool definitions, settings and context manifest of every model call — with **edit & rerun** that creates new immutable versions
- **Versioned virtual file workspace** with author attribution, diffs, restore and download
- **Structured agent messaging** (task assignment, review request/result, objection, handoff, …)
- **Cost & token accounting** per call, run, task, agent, project, model and day

## Quick start

Requirements: Node.js 20+ (tested on 22).

```bash
cd multi-agent-workspace
npm install
cp .env.example .env        # keys optional — the demo runs on the mock provider
npm run setup               # creates the SQLite DB and seeds templates + demo
npm run dev                 # http://localhost:3000
```

Then open **http://localhost:3000**, click the **Collaborative Software Build** project, and press **▶ Run demo**. Five agents (PM → Architect → Developer → Reviewer → QA → PM) will deliver a small feature end-to-end — including a review round that finds a real defect and a fix — while you watch every step in the **Activity** tab. No API keys or costs involved: the demo agents use the deterministic mock provider.

Or with Docker:

```bash
docker compose up --build   # http://localhost:3000
```

## Configuring real model providers

1. Put keys in `.env` (e.g. `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`). Keys live **only** in environment variables — the database stores just the *name* of the env var.
2. Open **Settings → Model configurations**. Seeded configs include Claude Fable 5, Claude Sonnet 5, an OpenAI-compatible GPT config, an Ollama local config, and a "Sol 5.6" placeholder you can point at any OpenAI-compatible endpoint.
3. Add your own: pick a provider (`anthropic` | `openai-compatible` | `mock`), a model ID, an optional base URL, the key env-var name, and per-MTok pricing (used for cost accounting).

See `docs/providers.md` for details on each adapter.

## Creating agents

**Agents → + New agent**: pick a template, a model, and a per-run budget. Each agent profile shows its role, system prompt, tool allowlist, permissions, run history and reliability metrics. Switch an agent's model at any time from the roster dropdown.

## Running a multi-agent project

1. **Dashboard → + New project** — set the objective, orchestration mode, and assign agents.
2. **Tasks → + New task** — set description, acceptance criteria, an owner, and optionally a **reviewer** (owner completion then routes the task to `awaiting_review`; a reviewer verdict containing "changes requested" sends it back).
3. Start work from the **Tasks** tab (run an agent on a task) or the **Agents** tab (free-form objective). Agents can also delegate: `create_task` with an `ownerRole` assigns work to teammates.
4. Watch the **Activity** tab; open any model call in the **Prompt Inspector**; pause/cancel runs from the **Agents** tab; approve gated actions in **Approvals**.

## Scripts

| Command | Purpose |
|---|---|
| `npm run dev` / `npm run build` / `npm start` | develop / build / serve |
| `npm run setup` | `prisma db push` + seed (idempotent) |
| `npm test` | full test suite (22 tests, isolated throwaway DB) |
| `npm run typecheck` | strict TypeScript check |

## Documentation

| Doc | Contents |
|---|---|
| [docs/architecture.md](docs/architecture.md) | System design, module map, run state machine (Mermaid) |
| [docs/data-model.md](docs/data-model.md) | Every entity and its role; immutability rules |
| [docs/orchestration.md](docs/orchestration.md) | The run loop, approvals, budgets, recovery, review flow |
| [docs/providers.md](docs/providers.md) | Provider setup: Anthropic, OpenAI-compatible, Ollama, mock |
| [docs/security.md](docs/security.md) | Threat model, protections, and honest limitations |
| [docs/testing.md](docs/testing.md) | Test layout and how to run it |
| [docs/deployment.md](docs/deployment.md) | Docker and production notes |
| [docs/limitations-roadmap.md](docs/limitations-roadmap.md) | Known gaps and next priorities |
| [BUILD_REPORT.md](BUILD_REPORT.md) | What was built vs. deferred, verification results |

## Project layout

```
multi-agent-workspace/
├── prisma/            # schema + idempotent seed (templates, models, demo)
├── src/
│   ├── app/           # Next.js pages + API routes (thin HTTP layer)
│   ├── components/    # UI (dashboard, workspace tabs, inspector, feeds)
│   ├── lib/
│   │   ├── providers/ # anthropic / openai-compatible / mock adapters
│   │   ├── tools/     # permissioned tool registry + executor
│   │   └── orchestrator/  # engine, prompt assembly, demo pipeline, rerun
│   └── instrumentation.ts # boot-time recovery of interrupted runs
├── tests/             # vitest: unit + integration + API e2e
└── docs/
```
