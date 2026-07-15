import { prisma } from '@/lib/db';
import { handle, ok, fail } from '@/lib/api';
import { parseJson } from '@/lib/json';

export const dynamic = 'force-dynamic';

/** Full state of one collaboration run, including every step's agent run. */
export async function GET(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  return handle(async () => {
    const { id } = await params;
    const run = await prisma.projectRun.findUnique({
      where: { id },
      include: {
        agentRuns: {
          orderBy: { createdAt: 'asc' },
          include: {
            agent: { select: { id: true, name: true, role: true } },
            modelCalls: { select: { id: true, seq: true, status: true, costUsd: true, createdAt: true } },
          },
        },
      },
    });
    if (!run) return fail('Project run not found', 404);
    const agents = await prisma.agent.findMany({
      where: { id: { in: [run.agentAId, run.agentBId, run.synthesizerId] } },
      select: { id: true, name: true, role: true },
    });
    return ok({
      ...run,
      steps: parseJson<Record<string, string>>(run.stepsJson, {}),
      roles: {
        agentA: agents.find((a) => a.id === run.agentAId) ?? null,
        agentB: agents.find((a) => a.id === run.agentBId) ?? null,
        synthesizer: agents.find((a) => a.id === run.synthesizerId) ?? null,
      },
    });
  });
}
