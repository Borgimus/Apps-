import { redactSecrets } from '../json';

/**
 * Pure Markdown/Obsidian formatting helpers. No filesystem or database access
 * lives here so the note shapes can be unit-tested in isolation.
 */

/** Characters that are unsafe in file names across the common vault targets. */
const UNSAFE_FILENAME = /[\\/:*?"<>|#^[\]]/g;

/**
 * Turn an arbitrary title into a vault-safe file name (without extension).
 * Obsidian additionally treats `#`, `^`, `[` and `]` specially in links, so
 * those are stripped too. Empty results fall back to `untitled`.
 */
export function safeFileName(title: string): string {
  const cleaned = title
    .replace(UNSAFE_FILENAME, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  return cleaned.length > 0 ? cleaned.slice(0, 120) : 'untitled';
}

type FrontmatterValue = string | number | boolean | null | undefined | string[];

function formatScalar(value: string | number | boolean): string {
  if (typeof value !== 'string') return String(value);
  // Quote strings that could otherwise be misread as YAML.
  if (value === '' || /[:#\-?{}[\],&*!|>'"%@`]/.test(value[0] ?? '') || /[:#]/.test(value)) {
    return JSON.stringify(value);
  }
  return value;
}

/** Build a YAML frontmatter block from a flat key/value map. */
export function frontmatter(fields: Record<string, FrontmatterValue>): string {
  const lines: string[] = ['---'];
  for (const [key, value] of Object.entries(fields)) {
    if (value === undefined || value === null) continue;
    if (Array.isArray(value)) {
      if (value.length === 0) continue;
      lines.push(`${key}:`);
      for (const item of value) lines.push(`  - ${formatScalar(item)}`);
    } else {
      lines.push(`${key}: ${formatScalar(value)}`);
    }
  }
  lines.push('---');
  return lines.join('\n');
}

/** An Obsidian wikilink, e.g. `[[Some Note]]` or `[[Some Note|alias]]`. */
export function wikilink(target: string, alias?: string): string {
  const t = safeFileName(target);
  return alias && alias !== t ? `[[${t}|${alias}]]` : `[[${t}]]`;
}

export interface NoteInput {
  title: string;
  frontmatter?: Record<string, FrontmatterValue>;
  body: string;
}

/**
 * Assemble a full note: frontmatter block, an H1 title, then the body.
 * Secrets are redacted from the rendered note as a defense-in-depth measure —
 * exported memory should never carry an API key into a synced vault.
 */
export function renderNote(note: NoteInput): string {
  const parts: string[] = [];
  if (note.frontmatter && Object.keys(note.frontmatter).length > 0) {
    parts.push(frontmatter(note.frontmatter));
  }
  parts.push(`# ${note.title.trim()}`);
  const body = note.body.trim();
  if (body.length > 0) parts.push(body);
  return redactSecrets(parts.join('\n\n')) + '\n';
}
