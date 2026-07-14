import { z } from 'zod';
import { prisma } from '@/lib/db';
import { handle, ok, fail, parseBody } from '@/lib/api';
import { createRun, processRun } from '@/lib/orchestrator/engine';

export const dynamic = 'force-dynamic';

export async function GET(req: Request) {
  return handle(async () => {
    const projectId = new URL(req.url).searchParams.get('projectId') ?? undefined;
    const runs = await prisma.agentRun.findMany({
      where: projectId ? { projectId } : {},
      orderBy: { createdAt: 'desc' },
      take: 100,
      include: {
        agent: { select: { id: true, name: true, role: true } },
        task: { select: { id: true, title: true } },
      },
    });
    return ok(runs);
  });
}

const startSchema = z.object({
  agentId: z.string().min(1),
  projectId: z.string().min(1),
  taskId: z.string().nullable().optional(),
  objective: z.string().min(1),
  maxIterations: z.number().int().min(1).max(50).optional(),
  idempotencyKey: z.string().optional(),
});

export async function POST(req: Request) {
  return handle(async () => {
    const parsed = await parseBody(req, startSchema);
    if ('error' in parsed) return parsed.error;
    const body = parsed.data;
    const link = await prisma.projectAgent.findUnique({
      where: { projectId_agentId: { projectId: body.projectId, agentId: body.agentId } },
    });
    if (!link) return fail('Agent is not assigned to this project', 400);
    if (body.taskId) {
      const task = await prisma.task.findUnique({ where: { id: body.taskId } });
      if (!task || task.projectId !== body.projectId) return fail('Task not found in this project', 400);
    }
    const run = await createRun({ ...body, taskId: body.taskId ?? null });
    void processRun(run.id).catch((err) => console.error('[api/runs] run failed', err));
    return ok(run, 202);
  });
}
