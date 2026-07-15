# GitHub App integration

The connector lets agents work with one installed GitHub repository while credentials remain on the server. It uses short-lived GitHub App installation tokens, project-level repository bindings, per-agent tool permissions, and the workspace approval queue.

## Before you start

Configure the GitHub App with these repository permissions:

- Contents: Read and write
- Pull requests: Read and write
- Checks: Read-only
- Actions: Read-only

Install the App only on the repository the workspace should access. Webhooks, OAuth callbacks, client secrets, and user authorization are not required for this local connector.

## Configure the local app

Keep the downloaded PEM outside the repository. In `.env`:

```dotenv
GITHUB_APP_ID="your-app-id"
GITHUB_INSTALLATION_ID="your-installation-id"
GITHUB_APP_PRIVATE_KEY_PATH="C:/Users/you/.secrets/your-app.pem"
GITHUB_REPOSITORY="owner/repository"
```

On Windows, forward slashes in the path avoid escape issues. Do not paste the PEM contents into `.env`.

Apply the Prisma schema and restart Next.js:

```bash
npm run setup
npm run dev
```

Open **GitHub** in the sidebar. The status panel verifies the App installation against GitHub. Select a project, choose the base branch, and choose a dedicated working branch such as `agent/my-task`.

## Agent access

The GitHub page can grant an agent:

- Read only: list the tree, read and search code, inspect branches, pull requests, diffs, and checks.
- Read + write: all read tools plus branch creation, file writes, atomic multi-file commits, and opening a draft pull request.
- Revoke: removes GitHub tools and permissions from the agent.

Granting write access does not bypass approval. Every remote write or pull-request action creates a high-risk approval request and pauses the run. An approved action is rechecked against the agent permissions before execution.

## Safety boundaries

- The server reads the PEM from `GITHUB_APP_PRIVATE_KEY_PATH`; it is never returned by an API or stored in Prisma.
- Installation tokens are short lived, kept only in process memory, and refreshed automatically.
- All API requests are restricted to the exact `GITHUB_REPOSITORY`.
- Writes are restricted to `agent/`, `agents/`, or `feature/` branches and, after project binding, to that project's exact working branch.
- Pull requests are always opened as drafts.
- File reads and writes have size limits, and atomic commits accept at most 20 files.
- The connector does not merge pull requests, delete branches, modify repository settings, expose secrets, clone code, or run shell commands.

## Troubleshooting

If the GitHub page reports a configuration error:

1. Confirm all four variables are present in `.env`.
2. Confirm the PEM path points to the downloaded private key and the Node process can read it.
3. Confirm the installation ID belongs to the App and includes `GITHUB_REPOSITORY`.
4. Confirm the repository uses exact `owner/name` capitalization.
5. Restart `npm run dev` after editing `.env`.

A project binding also verifies that the selected base branch exists. If a working branch already exists, agents can write to it. Otherwise an approved `github_create_branch` call creates it from the base branch.
