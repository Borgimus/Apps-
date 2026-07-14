import { prisma } from '@/lib/db';
import { handle, ok, fail } from '@/lib/api';

export const dynamic = 'force-dynamic';

export async function GET(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  return handle(async () => {
    const { id } = await params;
    const run = await prisma.agentRun.findUnique({
      where: { id },
      include: {
        agent: { select: { id: true, name: true, role: true }, },
        task: { select: { id: true, title: true, status: true } },
        modelCalls: { orderBy: { createdAt: 'asc' } },
        toolCalls: { orderBy: { createdAt: 'asc' } },
      },
    });
    if (!run) return fail('Run not found', 404);
    return ok(run);
  });
}
