import { promises as fs } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { prisma } from '@/lib/db';
import { frontmatter, renderNote, safeFileName, wikilink } from '@/lib/obsidian/markdown';
import { DEFAULT_VAULT_PATH, resolveExportRoot, resolveVaultPath } from '@/lib/obsidian/config';
import { exportWorkspaceToVault } from '@/lib/obsidian/vault';
import { makeFixture } from './helpers';

/**
 * Obsidian export tests. The vault is written to a throwaway temp directory so
 * nothing touches a real device path.
 */

describe('markdown helpers', () => {
  it('sanitizes unsafe filename characters', () => {
    expect(safeFileName('a/b:c*?"<>|')).toBe('a b c');
    expect(safeFileName('   ')).toBe('untitled');
    expect(safeFileName('Normal Title')).toBe('Normal Title');
  });

  it('builds wikilinks and frontmatter', () => {
    expect(wikilink('My Note')).toBe('[[My Note]]');
    expect(wikilink('My/Note', 'alias')).toBe('[[My Note|alias]]');
    const fm = frontmatter({ type: 'project', pinned: true, skip: undefined, tags: ['a', 'b'] });
    expect(fm).toContain('type: project');
    expect(fm).toContain('pinned: true');
    expect(fm).not.toContain('skip');
    expect(fm).toContain('  - a');
  });

  it('redacts secrets from rendered notes', () => {
    const note = renderNote({ title: 'X', body: 'key sk-ant-0123456789abcdef0123 here' });
    expect(note).toContain('[REDACTED]');
    expect(note).not.toContain('sk-ant-0123456789abcdef0123');
  });
});

describe('vault config', () => {
  const original = process.env.OBSIDIAN_VAULT_PATH;
  afterEach(() => {
    if (original === undefined) delete process.env.OBSIDIAN_VAULT_PATH;
    else process.env.OBSIDIAN_VAULT_PATH = original;
  });

  it('defaults to the documented device path', () => {
    delete process.env.OBSIDIAN_VAULT_PATH;
    expect(resolveVaultPath()).toBe(DEFAULT_VAULT_PATH);
    expect(resolveExportRoot()).toContain('Multi-Agent Workspace');
  });

  it('honors OBSIDIAN_VAULT_PATH and expands ~', () => {
    process.env.OBSIDIAN_VAULT_PATH = '~/my-vault';
    expect(resolveVaultPath()).toBe(path.join(os.homedir(), 'my-vault'));
  });
});

describe('exportWorkspaceToVault', () => {
  let vaultRoot: string;

  beforeEach(async () => {
    vaultRoot = await fs.mkdtemp(path.join(os.tmpdir(), 'obsidian-test-'));
  });
  afterEach(async () => {
    await fs.rm(vaultRoot, { recursive: true, force: true });
  });

  it('writes project memory and user data as markdown notes', async () => {
    const { project, agent } = await makeFixture();

    await prisma.agent.update({
      where: { id: agent.id },
      data: { memoryJson: JSON.stringify(['remembers to write tests first']) },
    });
    await prisma.projectMemory.create({
      data: { projectId: project.id, key: 'coding-style', content: 'Prefer small pure functions.', pinned: true },
    });
    await prisma.projectMemory.create({
      data: { projectId: project.id, key: 'scratch', content: 'temporary note' },
    });
    await prisma.decision.create({
      data: { projectId: project.id, title: 'Use SQLite', detail: 'Simplicity first.', madeBy: 'user' },
    });

    const summary = await exportWorkspaceToVault({ vaultRoot });

    expect(summary.memoryEntries).toBe(2);
    expect(summary.decisions).toBe(1);
    expect(summary.agents).toBeGreaterThanOrEqual(1);
    expect(summary.notesWritten).toBeGreaterThan(0);

    const projectDir = path.join(vaultRoot, 'Projects', safeFileName(project.name));
    const memory = await fs.readFile(path.join(projectDir, 'Memory.md'), 'utf8');
    expect(memory).toContain('coding-style');
    expect(memory).toContain('Prefer small pure functions.');
    expect(memory).toContain('📌 pinned');

    const decisions = await fs.readFile(path.join(projectDir, 'Decisions.md'), 'utf8');
    expect(decisions).toContain('Use SQLite');

    const agentNote = await fs.readFile(
      path.join(vaultRoot, 'Agents', `${safeFileName(agent.name)}.md`),
      'utf8',
    );
    expect(agentNote).toContain('remembers to write tests first');

    // Idempotent re-run doesn't throw and refreshes in place.
    const second = await exportWorkspaceToVault({ vaultRoot });
    expect(second.memoryEntries).toBe(2);
  });
});
