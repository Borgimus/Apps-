import { prisma } from '@/lib/db';
import { handle, ok } from '@/lib/api';

export const dynamic = 'force-dynamic';

/** Usage/cost rollups: totals, per project, per agent, per model, per day. */
export async function GET(req: Request) {
  return handle(async () => {
    const projectId = new URL(req.url).searchParams.get('projectId') ?? undefined;
    const where = projectId ? { projectId } : {};
    const records = await prisma.usageRecord.findMany({ where, orderBy: { createdAt: 'desc' }, take: 2000 });
    const agents = await prisma.agent.findMany({ select: { id: true, name: true } });
    const projects = await prisma.project.findMany({ select: { id: true, name: true } });
    const agentNames = Object.fromEntries(agents.map((a) => [a.id, a.name]));
    const projectNames = Object.fromEntries(projects.map((p) => [p.id, p.name]));

    const rollup = <K extends string>(key: (r: (typeof records)[number]) => K) => {
      const out: Record<string, { costUsd: number; inputTokens: number; outputTokens: number; calls: number }> = {};
      for (const r of records) {
        const k = key(r);
        const cur = (out[k] ??= { costUsd: 0, inputTokens: 0, outputTokens: 0, calls: 0 });
        cur.costUsd += r.costUsd;
        cur.inputTokens += r.inputTokens;
        cur.outputTokens += r.outputTokens;
        cur.calls += 1;
      }
      return out;
    };

    return ok({
      totals: {
        costUsd: records.reduce((n, r) => n + r.costUsd, 0),
        inputTokens: records.reduce((n, r) => n + r.inputTokens, 0),
        outputTokens: records.reduce((n, r) => n + r.outputTokens, 0),
        calls: records.length,
      },
      byProject: rollup((r) => projectNames[r.projectId] ?? r.projectId),
      byAgent: rollup((r) => (r.agentId ? agentNames[r.agentId] ?? r.agentId : 'user')),
      byModel: rollup((r) => `${r.provider}/${r.modelId}`),
      byDay: rollup((r) => r.createdAt.toISOString().slice(0, 10)),
    });
  });
}
