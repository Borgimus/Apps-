import { prisma } from '@/lib/db';
import { handle, ok, fail } from '@/lib/api';
import { runDemo } from '@/lib/orchestrator/demo';

export const dynamic = 'force-dynamic';

/** Kick off the seeded demonstration pipeline. Progress streams via SSE. */
export async function POST(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  return handle(async () => {
    const { id } = await params;
    const project = await prisma.project.findUnique({ where: { id } });
    if (!project) return fail('Project not found', 404);
    const running = await prisma.agentRun.count({
      where: { projectId: id, status: { in: ['queued', 'running'] } },
    });
    if (running > 0) return fail('Agents are already running on this project', 409);
    void runDemo(id).catch(async (err) => {
      console.error('[demo]', err);
    });
    return ok({ started: true }, 202);
  });
}
