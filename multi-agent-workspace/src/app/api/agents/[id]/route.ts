import { z } from 'zod';
import { prisma } from '@/lib/db';
import { handle, ok, fail, parseBody } from '@/lib/api';
import { ALL_TOOL_NAMES } from '@/lib/tools/defs';

export const dynamic = 'force-dynamic';

type Params = { params: Promise<{ id: string }> };

export async function GET(_req: Request, { params }: Params) {
  return handle(async () => {
    const { id } = await params;
    const agent = await prisma.agent.findUnique({
      where: { id },
      include: {
        modelConfig: true,
        projects: { include: { project: { select: { id: true, name: true } } } },
        runs: { orderBy: { createdAt: 'desc' }, take: 20 },
      },
    });
    if (!agent) return fail('Agent not found', 404);
    return ok(agent);
  });
}

const patchSchema = z.object({
  name: z.string().min(1).optional(),
  role: z.string().min(1).optional(),
  systemPrompt: z.string().min(1).optional(),
  modelConfigId: z.string().optional(), // switch models without rebuilding the agent
  tools: z.array(z.enum(ALL_TOOL_NAMES as [string, ...string[]])).optional(),
  permissions: z.record(z.boolean()).optional(),
  maxCostPerRunUsd: z.number().positive().max(1000).optional(),
  addToProjectIds: z.array(z.string()).optional(),
});

export async function PATCH(req: Request, { params }: Params) {
  return handle(async () => {
    const { id } = await params;
    const parsed = await parseBody(req, patchSchema);
    if ('error' in parsed) return parsed.error;
    const { tools, permissions, addToProjectIds, ...rest } = parsed.data;
    if (rest.modelConfigId) {
      const mc = await prisma.modelConfig.findUnique({ where: { id: rest.modelConfigId } });
      if (!mc) return fail('Unknown modelConfigId', 400);
    }
    const agent = await prisma.agent.update({
      where: { id },
      data: {
        ...rest,
        ...(tools ? { toolsJson: JSON.stringify(tools) } : {}),
        ...(permissions ? { permissionsJson: JSON.stringify(permissions) } : {}),
      },
    });
    if (addToProjectIds) {
      for (const projectId of addToProjectIds) {
        await prisma.projectAgent.upsert({
          where: { projectId_agentId: { projectId, agentId: id } },
          update: {},
          create: { projectId, agentId: id },
        });
      }
    }
    return ok(agent);
  });
}
