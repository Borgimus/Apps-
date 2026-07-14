import { z } from 'zod';
import { prisma } from '@/lib/db';
import { handle, ok, parseBody } from '@/lib/api';
import { emitActivity } from '@/lib/events';

export const dynamic = 'force-dynamic';

export async function GET() {
  return handle(async () => {
    const projects = await prisma.project.findMany({
      orderBy: { updatedAt: 'desc' },
      include: {
        agents: { include: { agent: { select: { id: true, name: true, role: true, status: true } } } },
        tasks: { select: { status: true } },
        approvals: { where: { status: 'pending' }, select: { id: true } },
        usage: { select: { costUsd: true, inputTokens: true, outputTokens: true } },
        runs: {
          where: { status: { in: ['running', 'queued', 'awaiting_approval', 'paused'] } },
          select: { id: true, status: true },
        },
      },
    });
    return ok(
      projects.map((p) => {
        const done = p.tasks.filter((t) => t.status === 'completed').length;
        return {
          id: p.id,
          name: p.name,
          objective: p.objective,
          status: p.status,
          orchestrationMode: p.orchestrationMode,
          budgetUsd: p.budgetUsd,
          updatedAt: p.updatedAt,
          agents: p.agents.map((a) => a.agent),
          taskCounts: { total: p.tasks.length, completed: done },
          pendingApprovals: p.approvals.length,
          activeRuns: p.runs.length,
          costUsd: p.usage.reduce((n, u) => n + u.costUsd, 0),
          tokens: p.usage.reduce((n, u) => n + u.inputTokens + u.outputTokens, 0),
        };
      }),
    );
  });
}

const createSchema = z.object({
  name: z.string().min(1).max(200),
  objective: z.string().default(''),
  instructions: z.string().default(''),
  orchestrationMode: z.enum(['manager', 'peer', 'review', 'debate', 'parallel', 'pipeline']).default('manager'),
  budgetUsd: z.number().positive().nullable().optional(),
  agentIds: z.array(z.string()).default([]),
});

export async function POST(req: Request) {
  return handle(async () => {
    const parsed = await parseBody(req, createSchema);
    if ('error' in parsed) return parsed.error;
    const { agentIds, ...data } = parsed.data;
    const workspace = await prisma.workspace.findFirstOrThrow();
    const project = await prisma.project.create({
      data: {
        ...data,
        budgetUsd: data.budgetUsd ?? null,
        workspaceId: workspace.id,
        agents: { create: agentIds.map((agentId) => ({ agentId })) },
      },
    });
    await emitActivity({
      projectId: project.id,
      actor: 'user',
      type: 'project_created',
      summary: `Project "${project.name}" created`,
      data: { agentIds },
    });
    return ok(project, 201);
  });
}
