import { z } from 'zod';
import { prisma } from '@/lib/db';
import { handle, ok, parseBody, fail } from '@/lib/api';
import { ALL_TOOL_NAMES } from '@/lib/tools/defs';

export const dynamic = 'force-dynamic';

export async function GET() {
  return handle(async () => {
    const agents = await prisma.agent.findMany({
      orderBy: { createdAt: 'asc' },
      include: {
        modelConfig: true,
        projects: { include: { project: { select: { id: true, name: true } } } },
        runs: { orderBy: { createdAt: 'desc' }, take: 5, select: { id: true, status: true, objective: true, costUsd: true, createdAt: true } },
      },
    });
    // Reliability metrics from run history
    const withMetrics = await Promise.all(
      agents.map(async (a) => {
        const [total, completed, failed] = await Promise.all([
          prisma.agentRun.count({ where: { agentId: a.id } }),
          prisma.agentRun.count({ where: { agentId: a.id, status: 'completed' } }),
          prisma.agentRun.count({ where: { agentId: a.id, status: 'failed' } }),
        ]);
        const cost = await prisma.usageRecord.aggregate({ where: { agentId: a.id }, _sum: { costUsd: true } });
        return { ...a, metrics: { totalRuns: total, completed, failed, totalCostUsd: cost._sum.costUsd ?? 0 } };
      }),
    );
    return ok(withMetrics);
  });
}

const createSchema = z.object({
  name: z.string().min(1).max(100),
  role: z.string().min(1).max(100),
  systemPrompt: z.string().min(1),
  modelConfigId: z.string().min(1),
  tools: z.array(z.enum(ALL_TOOL_NAMES as [string, ...string[]])).default([]),
  permissions: z
    .object({
      fileWrite: z.boolean().optional(),
      fileWriteRequiresApproval: z.boolean().optional(),
      fileDeleteRequiresApproval: z.boolean().optional(),
      network: z.boolean().optional(),
      githubRead: z.boolean().optional(),
      githubWrite: z.boolean().optional(),
      githubPullRequest: z.boolean().optional(),
    })
    .default({}),
  maxCostPerRunUsd: z.number().positive().max(1000).default(1),
  templateName: z.string().optional(),
  projectIds: z.array(z.string()).default([]),
});

export async function POST(req: Request) {
  return handle(async () => {
    const parsed = await parseBody(req, createSchema);
    if ('error' in parsed) return parsed.error;
    const body = parsed.data;
    const workspace = await prisma.workspace.findFirstOrThrow();
    const modelConfig = await prisma.modelConfig.findUnique({ where: { id: body.modelConfigId } });
    if (!modelConfig) return fail('Unknown modelConfigId', 400);
    const agent = await prisma.agent.create({
      data: {
        workspaceId: workspace.id,
        name: body.name,
        role: body.role,
        systemPrompt: body.systemPrompt,
        modelConfigId: body.modelConfigId,
        toolsJson: JSON.stringify(body.tools),
        permissionsJson: JSON.stringify(body.permissions),
        maxCostPerRunUsd: body.maxCostPerRunUsd,
        projects: { create: body.projectIds.map((projectId) => ({ projectId })) },
      },
    });
    return ok(agent, 201);
  });
}
