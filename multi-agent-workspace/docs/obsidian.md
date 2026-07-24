# Obsidian vault export

User data and project memory normally live in the app's database. This
migration lifts them out into a plain-Markdown [Obsidian](https://obsidian.md)
vault, so your memory travels with you — readable, greppable, and syncable to a
phone — instead of being locked inside SQLite.

## What gets exported

Everything is written under a `Multi-Agent Workspace/` subfolder of your vault:

| Note | Source |
|---|---|
| `Workspace.md` | Workspace name, instructions, daily budget, project index |
| `Agents/<Agent>.md` | Role, system prompt, and each agent's stored memory notes |
| `Projects/<Project>/Project.md` | Objective, instructions, status, orchestration mode |
| `Projects/<Project>/Memory.md` | **All `ProjectMemory` entries** (pinned first) |
| `Projects/<Project>/Decisions.md` | The decision log |
| `Projects/<Project>/Tasks.md` | Task board snapshot as a checklist |
| `Projects/<Project>/Files/<path>.md` | Latest content of each project file |

Every note carries YAML frontmatter (`type`, `id`, dates, …) so Obsidian's
search, Dataview, and graph view work over the exported data. Cross-references
between projects and agents use `[[wikilinks]]`.

## Running the migration

```bash
# Defaults the vault to `internal storage/documents/obsidian`
npm run migrate:obsidian

# Or point it at any vault root (absolute or ~-relative)
OBSIDIAN_VAULT_PATH="/path/to/MyVault" npm run migrate:obsidian
```

Configure the default in `.env`:

```bash
OBSIDIAN_VAULT_PATH="internal storage/documents/obsidian"
```

On a phone, `internal storage/documents/obsidian` is the folder Obsidian mobile
reads directly. When you run the migration in the app's environment, point
`OBSIDIAN_VAULT_PATH` at whatever mount corresponds to that device folder (or
run it locally and sync the resulting files across).

## Safety and re-runs

- **Idempotent.** Notes are overwritten in place. Re-running refreshes the vault
  without creating duplicates.
- **Secret-redacted.** API-key-shaped strings are stripped from note bodies
  before they are written, so exported memory never carries a credential into a
  synced vault.
- **Non-destructive.** The migration only reads from the database; nothing is
  deleted from the app.

## Programmatic use

```ts
import { exportWorkspaceToVault } from '@/lib/obsidian/vault';

const summary = await exportWorkspaceToVault(); // or { vaultRoot: '/custom' }
console.log(summary.memoryEntries, 'memory entries →', summary.vaultRoot);
```
