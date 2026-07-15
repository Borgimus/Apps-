import { z } from 'zod';
import { prisma } from '@/lib/db';
import { handle, ok, parseBody } from '@/lib/api';
import { emitActivity } from '@/lib/events';

export const dynamic = 'force-dynamic';

const createSchema = z.object({
  projectId: z.string().min(1),
  title: z.string().min(1).max(300),
  description: z.string().default(''),
  acceptanceCriteria: z.string().default(''),
  priority: z.enum(['low', 'medium', 'high', 'critical']).default('medium'),
  ownerAgentId: z.string().nullable().optional(),
  reviewerAgentId: z.string().nullable().optional(),
  parentTaskId: z.string().nullable().optional(),
  dependsOnTaskIds: z.array(z.string()).default([]),
});

export async function POST(req: Request) {
  return handle(async () => {
    const parsed = await parseBody(req, createSchema);
    if ('error' in parsed) return parsed.error;
    const { dependsOnTaskIds, ...data } = parsed.data;
    const task = await prisma.task.create({
      data: {
        ...data,
        ownerAgentId: data.ownerAgentId ?? null,
        reviewerAgentId: data.reviewerAgentId ?? null,
        parentTaskId: data.parentTaskId ?? null,
        status: data.ownerAgentId ? 'ready' : 'backlog',
        createdBy: 'user',
        dependencies: { create: dependsOnTaskIds.map((dependsOnId) => ({ dependsOnId })) },
      },
    });
    await emitActivity({
      projectId: task.projectId,
      actor: 'user',
      type: 'task_created',
      summary: `User created task "${task.title}"`,
      data: { taskId: task.id },
      refId: task.id,
    });
    return ok(task, 201);
  });
}
