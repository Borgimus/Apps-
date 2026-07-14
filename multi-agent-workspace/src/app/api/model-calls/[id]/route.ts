import { prisma } from '@/lib/db';
import { handle, ok, fail } from '@/lib/api';

export const dynamic = 'force-dynamic';

/** Full inspectable record of one model interaction (Prompt Inspector). */
export async function GET(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  return handle(async () => {
    const { id } = await params;
    const call = await prisma.modelCall.findUnique({
      where: { id },
      include: { toolCalls: { orderBy: { createdAt: 'asc' } } },
    });
    if (!call) return fail('Model call not found', 404);
    const agent = call.agentId
      ? await prisma.agent.findUnique({ where: { id: call.agentId }, select: { id: true, name: true, role: true } })
      : null;
    const versions = await prisma.modelCall.findMany({
      where: { OR: [{ parentCallId: call.parentCallId ?? call.id }, { id: call.parentCallId ?? call.id }] },
      select: { id: true, version: true, createdAt: true, status: true },
      orderBy: { version: 'asc' },
    });
    return ok({ ...call, agent, versions });
  });
}
