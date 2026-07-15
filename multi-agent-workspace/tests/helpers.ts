import { prisma } from '@/lib/db';

let counter = 0;

/** Create an isolated workspace/project/agent fixture for one test. */
export async function makeFixture(opts?: {
  tools?: string[];
  permissions?: Record<string, boolean>;
  maxCostPerRunUsd?: number;
  pricingPerMTok?: number;
  role?: string;
}) {
  counter++;
  const suffix = `${Date.now()}-${counter}`;
  const workspace = await prisma.workspace.create({
    data: { name: `test-ws-${suffix}`, instructions: 'Test workspace.' },
  });
  const modelConfig = await prisma.modelConfig.create({
    data: {
      name: `mock-${suffix}`,
      provider: 'mock',
      modelId: 'mock-1',
      temperature: 0,
      inputPricePerMTok: opts?.pricingPerMTok ?? 0,
      outputPricePerMTok: opts?.pricingPerMTok ?? 0,
    },
  });
  const project = await prisma.project.create({
    data: { workspaceId: workspace.id, name: `test-project-${suffix}`, objective: 'Test objective' },
  });
  const agent = await prisma.agent.create({
    data: {
      workspaceId: workspace.id,
      name: `TestAgent-${suffix}`,
      role: opts?.role ?? 'Generalist',
      systemPrompt: 'You are a test agent.',
      modelConfigId: modelConfig.id,
      toolsJson: JSON.stringify(
        opts?.tools ?? ['list_files', 'read_file', 'write_file', 'send_message', 'complete_task'],
      ),
      permissionsJson: JSON.stringify(opts?.permissions ?? { fileWrite: true, fileWriteRequiresApproval: false }),
      maxCostPerRunUsd: opts?.maxCostPerRunUsd ?? 1,
    },
  });
  await prisma.projectAgent.create({ data: { projectId: project.id, agentId: agent.id } });
  return { workspace, modelConfig, project, agent };
}

export async function addAgent(
  fixture: { workspace: { id: string }; modelConfig: { id: string }; project: { id: string } },
  name: string,
  role: string,
  tools: string[],
) {
  const agent = await prisma.agent.create({
    data: {
      workspaceId: fixture.workspace.id,
      name,
      role,
      systemPrompt: `You are ${name}.`,
      modelConfigId: fixture.modelConfig.id,
      toolsJson: JSON.stringify(tools),
      permissionsJson: JSON.stringify({ fileWrite: true, fileWriteRequiresApproval: false }),
      maxCostPerRunUsd: 1,
    },
  });
  await prisma.projectAgent.create({ data: { projectId: fixture.project.id, agentId: agent.id } });
  return agent;
}

/** Poll until fn() returns truthy or the timeout elapses. */
export async function waitFor<T>(fn: () => Promise<T | null | undefined | false>, timeoutMs = 10_000): Promise<T> {
  const start = Date.now();
  for (;;) {
    const value = await fn();
    if (value) return value;
    if (Date.now() - start > timeoutMs) throw new Error('waitFor timed out');
    await new Promise((r) => setTimeout(r, 50));
  }
}

export const BUILDER_TOOLS = [
  'list_files', 'read_file', 'write_file', 'create_task', 'update_task',
  'send_message', 'record_decision', 'request_review', 'request_approval', 'complete_task',
];
