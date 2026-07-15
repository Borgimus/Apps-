# GitHub integration

Agents can read the connected repository and *propose* changes — branches, commits, draft pull requests — with every mutation stopping for your approval. The integration is a GitHub App connection restricted to exactly one repository.

## Setup

1. Create a **GitHub App** (github.com → Settings → Developer settings → GitHub Apps):
   - Repository permissions: **Contents: Read & write**, **Pull requests: Read & write**, **Checks: Read-only**, Metadata: Read-only.
   - No webhook needed. Generate and download a **private key** (.pem).
2. **Install** the App on your account, selecting **only** the target repository. Note the installation ID (the number in the installation page URL).
3. Configure `.env` (see `.env.example`): `GITHUB_APP_ID`, `GITHUB_INSTALLATION_ID`, `GITHUB_APP_PRIVATE_KEY_PATH` (path to the .pem on disk), `GITHUB_REPOSITORY` (`owner/repo`). Restart the app.
4. Open a project → **Settings → GitHub repository** → **Test connection**. This verifies configuration, authentication and repository identity end to end, and stores only identity + status (never credentials).
5. On the **Agents** page, give each agent a GitHub access level:
   - **read-only** — the 7 read tools, no approvals needed
   - **read + write** — adds branch creation and commits, every call approval-gated
   - **read + write + draft PRs** — adds draft-PR creation, approval-gated

## Security model

| Layer | Enforcement |
|---|---|
| Credentials | Private key read server-side from `GITHUB_APP_PRIVATE_KEY_PATH` only; never logged, persisted, or returned to browser/model. Installation tokens are short-lived, cached in process memory, and **issued restricted to the one repository**. |
| Repository scope | Every API path is validated against `GITHUB_REPOSITORY` before any request. The active project must also have a verified repository connection matching that repository. Anything else fails closed with zero network access. |
| Permissions | `githubRead` / `githubWrite` / `githubPullRequest` flags per agent, enforced inside the central executor. |
| Approvals | **Every mutation** (create branch, write file, commit files, open draft PR) requires human approval — permissions let an agent *request*, approval lets it *execute*. Reads run without approval when `githubRead` is set. |
| Branch guard | Writes only to the project's exact configured working branch, which must start with `agent/`, `agents/`, or `feature/`. Branch creation must use the configured base, and draft PRs must target it. Protected branches remain hard-refused regardless of approval. |
| Path and payload guard | `.github/workflows/**`, `.github/actions/**`, `.git/**` and path traversal are always refused. Single-file writes are capped at 500 KB; atomic commits are capped at 20 files and 1 MB total. |
| Missing by design | No merge tool, no branch deletion, no repository administration, no secret access. PRs are always **draft**; merging is a human action on GitHub. |
| Errors | All GitHub error text passes secret redaction before reaching the model, timeline, or browser. |

## Tools

Reads (`githubRead`): `github_list_tree`, `github_read_file`, `github_search_code`, `github_read_branch`, `github_read_pull_request`, `github_read_diff`, `github_read_checks`.
Writes (`githubWrite`, approval-gated): `github_create_branch`, `github_write_file`, `github_commit_files`.
PRs (`githubPullRequest`, approval-gated): `github_open_draft_pull_request`.

All calls flow through the central executor: zod-validated, permission-checked, recorded as immutable ToolCall rows, and visible in the Activity timeline and Prompt Inspector. Approval requests show the exact branch, paths and content in the payload before you approve.

## Typical flow

Give a task-based agent (Agents tab → run with an objective) read+write+PR access, then an objective like: *"Read src/… on the base branch, create branch agent/fix-x, commit the fix, and open a draft PR."* The agent reads freely, then pauses in **Approvals** before each mutation — you see the diff-able payload and approve, reject with a note, or let it adapt.

## Known limitations

- One repository per workspace (by design — the token is scoped at issuance).
- `github_write_file`/`github_commit_files` create/update files; file *deletion* via agents is intentionally unsupported.
- Base-branch selection is a free-text field (no branch dropdown yet).
- Approval granularity is per tool call; a multi-step "plan approval" is roadmap.
- GitHub API rate limits apply per installation; heavy tree/search use on big repos may throttle (surfaced as visible retryable errors).
