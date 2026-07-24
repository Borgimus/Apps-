import { promises as fs } from 'node:fs';
import path from 'node:path';
import { prisma } from '../db';
import { parseJson } from '../json';
import { resolveExportRoot } from './config';
import { renderNote, safeFileName, wikilink } from './markdown';

/**
 * Export the workspace's user data and project memory from the database into an
 * Obsidian vault of Markdown notes.
 *
 * Layout under `<vault>/Multi-Agent Workspace/`:
 *   Workspace.md                     — workspace instructions + budget
 *   Agents/<Agent>.md                — role, prompt, and agent memory notes
 *   Projects/<Project>/Project.md    — objective, instructions, status
 *   Projects/<Project>/Memory.md     — all ProjectMemory entries (pinned first)
 *   Projects/<Project>/Decisions.md  — the decision log
 *   Projects/<Project>/Tasks.md      — task board snapshot
 *   Projects/<Project>/Files/<path>.md — latest content of each project file
 *
 * The export is additive and idempotent: notes are overwritten in place, so
 * re-running the migration refreshes the vault without creating duplicates.
 */

export interface ExportSummary {
  vaultRoot: string;
  workspaces: number;
  projects: number;
  memoryEntries: number;
  agents: number;
  decisions: number;
  tasks: number;
  files: number;
  notesWritten: number;
}

interface AgentMemoryNote {
  note?: string;
  content?: string;
  text?: string;
  createdAt?: string;
}

async function writeNote(
  root: string,
  relPath: string,
  content: string,
  summary: ExportSummary,
): Promise<void> {
  const full = path.join(root, relPath);
  await fs.mkdir(path.dirname(full), { recursive: true });
  await fs.writeFile(full, content, 'utf8');
  summary.notesWritten += 1;
}

function isoDay(d: Date): string {
  return d.toISOString().slice(0, 10);
}

export async function exportWorkspaceToVault(opts?: {
  vaultRoot?: string;
}): Promise<ExportSummary> {
  const root = opts?.vaultRoot ?? resolveExportRoot();
  const summary: ExportSummary = {
    vaultRoot: root,
    workspaces: 0,
    projects: 0,
    memoryEntries: 0,
    agents: 0,
    decisions: 0,
    tasks: 0,
    files: 0,
    notesWritten: 0,
  };

  const workspaces = await prisma.workspace.findMany({
    include: { agents: true, projects: true },
    orderBy: { createdAt: 'asc' },
  });

  for (const workspace of workspaces) {
    summary.workspaces += 1;

    await writeNote(
      root,
      'Workspace.md',
      renderNote({
        title: workspace.name,
        frontmatter: {
          type: 'workspace',
          id: workspace.id,
          created: isoDay(workspace.createdAt),
          dailyBudgetUsd: workspace.dailyBudgetUsd ?? undefined,
        },
        body: [
          '## Instructions',
          workspace.instructions.trim() || '_None._',
          '## Projects',
          workspace.projects.length > 0
            ? workspace.projects.map((p) => `- ${wikilink(p.name)}`).join('\n')
            : '_No projects._',
        ].join('\n\n'),
      }),
      summary,
    );

    // --- Agents (workspace-scoped, includes per-agent memory) ---------------
    for (const agent of workspace.agents) {
      summary.agents += 1;
      const memNotes = parseJson<AgentMemoryNote[] | string[]>(agent.memoryJson, []);
      const memoryBody =
        Array.isArray(memNotes) && memNotes.length > 0
          ? memNotes
              .map((m) => {
                if (typeof m === 'string') return `- ${m}`;
                const text = m.note ?? m.content ?? m.text ?? JSON.stringify(m);
                return m.createdAt ? `- (${isoDay(new Date(m.createdAt))}) ${text}` : `- ${text}`;
              })
              .join('\n')
          : '_No stored memory._';

      await writeNote(
        root,
        path.join('Agents', `${safeFileName(agent.name)}.md`),
        renderNote({
          title: agent.name,
          frontmatter: {
            type: 'agent',
            id: agent.id,
            role: agent.role,
            status: agent.status,
            created: isoDay(agent.createdAt),
          },
          body: [
            `**Role:** ${agent.role}`,
            '## System prompt',
            agent.systemPrompt.trim() || '_None._',
            '## Memory',
            memoryBody,
          ].join('\n\n'),
        }),
        summary,
      );
    }
  }

  // --- Projects and their memory ------------------------------------------
  const projects = await prisma.project.findMany({
    orderBy: { createdAt: 'asc' },
  });

  for (const project of projects) {
    summary.projects += 1;
    const dir = path.join('Projects', safeFileName(project.name));

    await writeNote(
      root,
      path.join(dir, 'Project.md'),
      renderNote({
        title: project.name,
        frontmatter: {
          type: 'project',
          id: project.id,
          status: project.status,
          orchestrationMode: project.orchestrationMode,
          budgetUsd: project.budgetUsd ?? undefined,
          created: isoDay(project.createdAt),
          updated: isoDay(project.updatedAt),
        },
        body: [
          '## Objective',
          project.objective.trim() || '_None._',
          '## Instructions',
          project.instructions.trim() || '_None._',
          '## Contents',
          [
            `- ${wikilink('Memory')}`,
            `- ${wikilink('Decisions')}`,
            `- ${wikilink('Tasks')}`,
          ].join('\n'),
        ].join('\n\n'),
      }),
      summary,
    );

    // Project memory — the core of the migration.
    const memory = await prisma.projectMemory.findMany({
      where: { projectId: project.id },
      orderBy: [{ pinned: 'desc' }, { updatedAt: 'desc' }],
    });
    summary.memoryEntries += memory.length;
    const memoryBody =
      memory.length > 0
        ? memory
            .map((m) => {
              const flags: string[] = [];
              if (m.pinned) flags.push('📌 pinned');
              if (m.expiresAt) flags.push(`expires ${isoDay(m.expiresAt)}`);
              const meta = flags.length > 0 ? ` _(${flags.join(', ')})_` : '';
              return `### ${m.key}${meta}\n\n${m.content.trim()}`;
            })
            .join('\n\n')
        : '_No project memory recorded._';

    await writeNote(
      root,
      path.join(dir, 'Memory.md'),
      renderNote({
        title: `${project.name} — Memory`,
        frontmatter: { type: 'project-memory', project: project.name, entries: memory.length },
        body: memoryBody,
      }),
      summary,
    );

    // Decision log.
    const decisions = await prisma.decision.findMany({
      where: { projectId: project.id },
      orderBy: { createdAt: 'asc' },
    });
    summary.decisions += decisions.length;
    await writeNote(
      root,
      path.join(dir, 'Decisions.md'),
      renderNote({
        title: `${project.name} — Decisions`,
        frontmatter: { type: 'decision-log', project: project.name, entries: decisions.length },
        body:
          decisions.length > 0
            ? decisions
                .map(
                  (d) =>
                    `### ${d.title}\n\n- **When:** ${isoDay(d.createdAt)}\n- **By:** ${d.madeBy}\n\n${d.detail.trim() || '_No detail._'}`,
                )
                .join('\n\n')
            : '_No decisions recorded._',
      }),
      summary,
    );

    // Task board snapshot.
    const tasks = await prisma.task.findMany({
      where: { projectId: project.id },
      orderBy: { createdAt: 'asc' },
    });
    summary.tasks += tasks.length;
    await writeNote(
      root,
      path.join(dir, 'Tasks.md'),
      renderNote({
        title: `${project.name} — Tasks`,
        frontmatter: { type: 'task-board', project: project.name, entries: tasks.length },
        body:
          tasks.length > 0
            ? tasks
                .map((t) => {
                  const done = t.status === 'completed';
                  const desc = t.description.trim() ? `\n  ${t.description.trim().replace(/\n/g, '\n  ')}` : '';
                  return `- [${done ? 'x' : ' '}] **${t.title}** _(${t.status}, ${t.priority})_${desc}`;
                })
                .join('\n')
            : '_No tasks._',
      }),
      summary,
    );

    // Project files — latest version content, as attachments in the vault.
    const files = await prisma.projectFile.findMany({
      where: { projectId: project.id, deleted: false },
      orderBy: { path: 'asc' },
    });
    for (const file of files) {
      const latest = await prisma.fileVersion.findFirst({
        where: { fileId: file.id },
        orderBy: { version: 'desc' },
      });
      summary.files += 1;
      const safeRel = file.path
        .split('/')
        .map((seg) => safeFileName(seg))
        .join('/');
      await writeNote(
        root,
        path.join(dir, 'Files', `${safeRel}.md`),
        renderNote({
          title: file.path,
          frontmatter: {
            type: 'project-file',
            project: project.name,
            path: file.path,
            version: file.latestVersion,
            updated: isoDay(file.updatedAt),
          },
          body: latest
            ? ['```', latest.content.replace(/```/g, '``​`'), '```'].join('\n')
            : '_No content._',
        }),
        summary,
      );
    }
  }

  return summary;
}
