import { prisma } from '@/lib/db';
import { handle, ok } from '@/lib/api';

export const dynamic = 'force-dynamic';

export async function GET(req: Request) {
  return handle(async () => {
    const url = new URL(req.url);
    const status = url.searchParams.get('status') ?? undefined;
    const projectId = url.searchParams.get('projectId') ?? undefined;
    const approvals = await prisma.approvalRequest.findMany({
      where: { ...(status ? { status } : {}), ...(projectId ? { projectId } : {}) },
      orderBy: { createdAt: 'desc' },
      take: 100,
      include: { project: { select: { id: true, name: true } } },
    });
    const agents = await prisma.agent.findMany({ select: { id: true, name: true, role: true } });
    const agentMap = Object.fromEntries(agents.map((a) => [a.id, a]));
    return ok(approvals.map((a) => ({ ...a, agent: a.agentId ? agentMap[a.agentId] ?? null : null })));
  });
}
