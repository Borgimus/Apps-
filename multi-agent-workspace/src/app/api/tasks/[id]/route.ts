import { z } from 'zod';
import { prisma } from '@/lib/db';
import { handle, ok, fail, parseBody } from '@/lib/api';
import { emitActivity } from '@/lib/events';

export const dynamic = 'force-dynamic';

type Params = { params: Promise<{ id: string }> };

export async function GET(_req: Request, { params }: Params) {
  return handle(async () => {
    const { id } = await params;
    const task = await prisma.task.findUnique({
      where: { id },
      include: {
        ownerAgent: { select: { id: true, name: true, role: true } },
        reviewerAgent: { select: { id: true, name: true, role: true } },
        runs: { orderBy: { createdAt: 'desc' }, include: { agent: { select: { name: true } } } },
        messages: { orderBy: { createdAt: 'asc' } },
        subtasks: true,
        dependencies: { include: { dependsOn: { select: { id: true, title: true, status: true } } } },
      },
    });
    if (!task) return fail('Task not found', 404);
    return ok(task);
  });
}

const patchSchema = z.object({
  title: z.string().min(1).optional(),
  description: z.string().optional(),
  acceptanceCriteria: z.string().optional(),
  status: z
    .enum(['backlog', 'ready', 'in_progress', 'blocked', 'awaiting_review', 'awaiting_approval', 'completed', 'failed', 'cancelled'])
    .optional(),
  priority: z.enum(['low', 'medium', 'high', 'critical']).optional(),
  ownerAgentId: z.string().nullable().optional(),
  reviewerAgentId: z.string().nullable().optional(),
  resultSummary: z.string().optional(),
});

export async function PATCH(req: Request, { params }: Params) {
  return handle(async () => {
    const { id } = await params;
    const parsed = await parseBody(req, patchSchema);
    if ('error' in parsed) return parsed.error;
    const before = await prisma.task.findUnique({ where: { id } });
    if (!before) return fail('Task not found', 404);
    const task = await prisma.task.update({ where: { id }, data: parsed.data });
    if (parsed.data.status && parsed.data.status !== before.status) {
      await emitActivity({
        projectId: task.projectId,
        actor: 'user',
        type: 'status_change',
        summary: `User moved task "${task.title}" ${before.status} → ${task.status}`,
        data: { taskId: id, from: before.status, to: task.status },
        refId: id,
      });
    }
    return ok(task);
  });
}
