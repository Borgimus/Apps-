import path from 'node:path';
import os from 'node:os';

/**
 * Obsidian vault configuration.
 *
 * User data and project memory are exported out of the database into a plain
 * Markdown vault so they live in Obsidian rather than only inside the app.
 * The vault location is configurable — on a phone this is typically
 * `internal storage/documents/obsidian`, which Obsidian mobile reads directly.
 *
 * Resolution order for the vault root:
 *   1. `OBSIDIAN_VAULT_PATH` env var (absolute or `~`-relative), if set.
 *   2. The documented default, `internal storage/documents/obsidian`.
 *
 * A leading `~` is expanded to the current user's home directory so the same
 * value works on a workstation and on device sync targets.
 */

/** The default vault location when nothing is configured. */
export const DEFAULT_VAULT_PATH = path.join(
  'internal storage',
  'documents',
  'obsidian',
);

/** Subfolder inside the vault that all exported notes are written under. */
export const VAULT_SUBFOLDER = 'Multi-Agent Workspace';

function expandHome(p: string): string {
  if (p === '~') return os.homedir();
  if (p.startsWith('~/') || p.startsWith('~\\')) {
    return path.join(os.homedir(), p.slice(2));
  }
  return p;
}

/** Absolute (or configured-relative) path to the Obsidian vault root. */
export function resolveVaultPath(): string {
  const configured = process.env.OBSIDIAN_VAULT_PATH?.trim();
  const raw = configured && configured.length > 0 ? configured : DEFAULT_VAULT_PATH;
  return expandHome(raw);
}

/** Absolute path to the workspace export folder inside the vault. */
export function resolveExportRoot(): string {
  return path.join(resolveVaultPath(), VAULT_SUBFOLDER);
}
