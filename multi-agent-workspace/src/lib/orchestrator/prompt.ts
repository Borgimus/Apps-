import type { Agent, ModelConfig, Project, Task, Workspace } from '@prisma/client';
import { prisma } from '../db';

/**
 * Prompt assembly + context selection.
 *
 * Context is layered and *selective* — we never dump the whole project history
 * into a model call. What was included (and why) is recorded on the ModelCall
 * so the Prompt Inspector can show exactly what the model saw.
 */

export interface AssembledContext {
  system: string;
  taskPrompt: string;
  /** Machine-readable record of what context was included. */
  contextManifest: Record<string, unknown>;
}

export async function assembleContext(opts: {
  workspace: Workspace;
  project: Project;
  agent: Agent & { modelConfig: ModelConfig };
  task: Task | null;
  objective: string;
}): Promise<AssembledContext> {
  const { workspace, project, agent, task, objective } = opts;

  const roster = await prisma.projectAgent.findMany({
    where: { projectId: project.id },
    include: { agent: true },
  });
  const files = await prisma.projectFile.findMany({
    where: { projectId: project.id, deleted: false },
    orderBy: { path: 'asc' },
    take: 50,
  });
  const pinnedMemory = await prisma.projectMemory.findMany({
    where: { projectId: project.id, pinned: true },
    take: 20,
  });
  const repoConnection = await prisma.repoConnection.findUnique({
    where: { projectId: project.id },
  });
  // Recent messages addressed to this agent or broadcast — not the full history.
  const recentMessages = await prisma.message.findMany({
    where: {
      projectId: project.id,
      OR: [{ toAgentId: agent.id }, { toAgentId: null }],
      ...(task ? { OR: [{ toAgentId: agent.id }, { toAgentId: null }, { taskId: task.id }] } : {}),
    },
    orderBy: { createdAt: 'desc' },
    take: 8,
    include: { task: { select: { title: true } } },
  });
  const agentNames = new Map(roster.map((r) => [r.agent.id, r.agent.name]));

  const system = [
    agent.systemPrompt.trim(),
    '',
    `ROLE: ${agent.role}`,
    `You are "${agent.name}", one of several AI agents collaborating on this project.`,
    '',
    '## Workspace instructions',
    workspace.instructions || '(none)',
    '',
    '## Project',
    `Name: ${project.name}`,
    `Objective: ${project.objective || '(none)'}`,
    `Instructions: ${project.instructions || '(none)'}`,
    `Orchestration mode: ${project.orchestrationMode}`,
    '',
    '## Repository connection',
    repoConnection?.status === 'connected'
      ? [
          `Repository: ${repoConnection.owner}/${repoConnection.repo}`,
          `Base branch: ${repoConnection.baseBranch}`,
          `Working branch: ${repoConnection.workingBranch || '(not configured; repository writes are unavailable)'}`,
        ].join('\n')
      : 'No verified repository connection is configured for this project.',
    '',
    '## Team',
    roster.map((r) => `- ${r.agent.name} (${r.agent.role})${r.agent.id === agent.id ? ' ← you' : ''}`).join('\n') ||
      '(no agents assigned)',
    '',
    '## Working rules',
    '- Work only through the provided tools; every action is logged and visible to the human operator.',
    '- Communicate with other agents via send_message with an appropriate message type.',
    '- Create subtasks with create_task and delegate by role when work belongs to someone else.',
    '- When you finish, call complete_task with a concise factual summary.',
    '- GitHub mutation tools automatically create their own human approval gate. Call the GitHub tool directly; never call request_approval separately for a GitHub mutation.',
    '- For actions outside your permissions that do not have an automatic tool approval gate, call request_approval instead of working around the restriction.',
    '- Never fabricate file contents or results; read files before editing them, and pass baseVersion when writing.',
  ].join('\n');

  const taskPromptParts: string[] = [];
  if (task) {
    taskPromptParts.push(
      `# Current task: ${task.title}`,
      task.description ? `\n${task.description}` : '',
      task.acceptanceCriteria ? `\n## Acceptance criteria\n${task.acceptanceCriteria}` : '',
    );
  }
  taskPromptParts.push(`\n## Objective for this run\n${objective}`);
  if (pinnedMemory.length > 0) {
    taskPromptParts.push(
      '\n## Pinned project memory',
      pinnedMemory.map((m) => `- ${m.key}: ${m.content}`).join('\n'),
    );
  }
  if (files.length > 0) {
    taskPromptParts.push(
      '\n## Project files (latest versions)',
      files.map((f) => `- ${f.path} (v${f.latestVersion})`).join('\n'),
    );
  }
  if (recentMessages.length > 0) {
    taskPromptParts.push(
      '\n## Recent messages for you',
      [...recentMessages]
        .reverse()
        .map((m) => {
          const from = m.fromAgentId ? agentNames.get(m.fromAgentId) ?? 'agent' : 'user';
          return `- [${m.type}] from ${from}${m.task ? ` (task: ${m.task.title})` : ''}: ${m.content.slice(0, 500)}`;
        })
        .join('\n'),
    );
  }

  return {
    system,
    taskPrompt: taskPromptParts.filter(Boolean).join('\n'),
    contextManifest: {
      layers: ['agent.systemPrompt', 'workspace.instructions', 'project', 'roster', 'workingRules'],
      task: task ? { id: task.id, title: task.title } : null,
      repositoryConnection: repoConnection
        ? {
            owner: repoConnection.owner,
            repo: repoConnection.repo,
            baseBranch: repoConnection.baseBranch,
            workingBranch: repoConnection.workingBranch,
            status: repoConnection.status,
          }
        : null,
      pinnedMemoryKeys: pinnedMemory.map((m) => m.key),
      fileList: files.map((f) => f.path),
      includedMessageIds: recentMessages.map((m) => m.id),
    },
  };
}
