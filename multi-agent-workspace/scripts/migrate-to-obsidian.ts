import { prisma } from '../src/lib/db';
import { resolveExportRoot, resolveVaultPath } from '../src/lib/obsidian/config';
import { exportWorkspaceToVault } from '../src/lib/obsidian/vault';

/**
 * Migrate user data and project memory out of the database into an Obsidian
 * vault of Markdown notes.
 *
 * Usage:
 *   npm run migrate:obsidian
 *   OBSIDIAN_VAULT_PATH="/path/to/vault" npm run migrate:obsidian
 *
 * The vault path defaults to `internal storage/documents/obsidian`; override it
 * with OBSIDIAN_VAULT_PATH (absolute or `~`-relative). Notes are written under a
 * `Multi-Agent Workspace/` subfolder and overwritten idempotently on re-run.
 */

async function main() {
  const vault = resolveVaultPath();
  const exportRoot = resolveExportRoot();
  console.log(`Vault root:  ${vault}`);
  console.log(`Export into: ${exportRoot}`);
  console.log('Migrating user data and project memory to Obsidian...\n');

  const summary = await exportWorkspaceToVault();

  console.log('Done. Wrote:');
  console.log(`  ${summary.notesWritten} notes`);
  console.log(`  ${summary.workspaces} workspace(s), ${summary.agents} agent(s)`);
  console.log(`  ${summary.projects} project(s)`);
  console.log(`  ${summary.memoryEntries} project-memory entrie(s)`);
  console.log(`  ${summary.decisions} decision(s), ${summary.tasks} task(s), ${summary.files} file(s)`);
  console.log(`\nOpen the vault in Obsidian at: ${summary.vaultRoot}`);
}

main()
  .catch((e) => {
    console.error('Migration failed:', e);
    process.exit(1);
  })
  .finally(() => prisma.$disconnect());
