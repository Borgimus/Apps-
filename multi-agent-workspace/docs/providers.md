# Provider setup

All providers implement one interface (`src/lib/providers/types.ts`): normalized messages, tool definitions, usage, and typed errors (`auth`, `rate_limit`, `timeout`, `overloaded`, `invalid_request`, `network`) with a `retryable` flag. The registry wraps every call with retry + exponential backoff.

Model configurations live in **Settings → Model configurations** (or the seed). A configuration = provider + model ID + optional base URL + key env-var name + sampling defaults + per-MTok pricing. Agents reference a configuration and can be repointed at any time.

## Anthropic (`provider: anthropic`)

- Native Messages API (`/v1/messages`) with tool use.
- Set `ANTHROPIC_API_KEY` in `.env` (or any env var you name in the config).
- Seeded configs: **Claude Fable 5** (`claude-fable-5`), **Claude Sonnet 5** (`claude-sonnet-5`). Adjust model IDs and pricing to match your account.

## OpenAI-compatible (`provider: openai-compatible`)

One adapter covers every chat-completions-compatible endpoint:

| Target | baseUrl | key |
|---|---|---|
| OpenAI | *(default)* `https://api.openai.com/v1` | `OPENAI_API_KEY` |
| OpenRouter | `https://openrouter.ai/api/v1` | your OpenRouter key env var |
| Ollama (local) | `http://localhost:11434/v1` | none needed |
| vLLM / LM Studio / gateways | your endpoint + `/v1` | as required |

## Placeholder models (e.g. "Sol 5.6")

Models without a public API yet are represented as ordinary configurations with placeholder identifiers (`sol-5.6`, `SOL_BASE_URL`, `SOL_API_KEY`). When a real endpoint exists, edit the configuration — no agent or project changes needed.

## Mock (`provider: mock`)

Free, deterministic, offline. Drives the seeded demonstration and the test suite:

- Role-based scripts (Project Manager decomposes, Architect designs, Developer implements/fixes, Reviewer critiques then approves, QA verifies).
- Test markers in the run objective: `[test:noop]`, `[test:write]`, `[test:loop]`, `[test:error]`, `[test:slow]`, `[test:forbidden-tool]`.
- Deterministic token usage (≈ chars/4) so cost accounting is exercised.

## Adding a new provider

1. Implement `ProviderAdapter` in `src/lib/providers/yourprovider.ts` (map normalized messages/tools to the provider's wire format; classify errors).
2. Register it in `registry.ts`.
3. Add a `ModelConfig` row (Settings UI or seed).

Nothing else changes — the engine, tools, UI and accounting are provider-agnostic.
