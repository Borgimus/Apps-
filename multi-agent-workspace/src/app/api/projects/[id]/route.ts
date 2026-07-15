import { z } from 'zod';
import { prisma } from '@/lib/db';
import { handle, ok, fail, parseBody } from '@/lib/api';

export const dynamic = 'force-dynamic';

type Params = { params: Promise<{ id: string }> };

export async function GET(_req: Request, { params }: Params) {
  return handle(async () => {
    const { id } = await params;
    const project = await prisma.project.findUnique({
      where: { id },
      include: {
        workspace: { select: { name: true, instructions: true } },
        agents: { include: { agent: { include: { modelConfig: true } } } },
        tasks: {
          orderBy: { createdAt: 'asc' },
          include: {
            ownerAgent: { select: { id: true, name: true, role: true } },
            reviewerAgent: { select: { id: true, name: true, role: true } },
            dependencies: true,
          },
        },
        files: { where: { deleted: false }, orderBy: { path: 'asc' } },
        decisions: { orderBy: { createdAt: 'desc' } },
        approvals: { orderBy: { createdAt: 'desc' } },
        messages: { orderBy: { createdAt: 'asc' } },
        memory: true,
        runs: {
          orderBy: { createdAt: 'desc' },
          include: { agent: { select: { id: true, name: true, role: true } }, task: { select: { id: true, title: true } } },
        },
        projectRuns: { orderBy: { createdAt: 'desc' } },
      },
    });
    if (!project) return fail('Project not found', 404);
    const usage = await prisma.usageRecord.aggregate({
      where: { projectId: id },
      _sum: { costUsd: true, inputTokens: true, outputTokens: true },
    });
    return ok({ ...project, usageTotals: usage._sum });
  });
}

const patchSchema = z.object({
  name: z.string().min(1).optional(),
  objective: z.string().optional(),
  instructions: z.string().optional(),
  status: z.enum(['active', 'paused', 'completed', 'archived']).optional(),
  orchestrationMode: z.enum(['manager', 'peer', 'review', 'debate', 'parallel', 'pipeline']).optional(),
  budgetUsd: z.number().positive().nullable().optional(),
});

export async function PATCH(req: Request, { params }: Params) {
  return handle(async () => {
    const { id } = await params;
    const parsed = await parseBody(req, patchSchema);
    if ('error' in parsed) return parsed.error;
    const project = await prisma.project.update({ where: { id }, data: parsed.data });
    return ok(project);
  });
}
