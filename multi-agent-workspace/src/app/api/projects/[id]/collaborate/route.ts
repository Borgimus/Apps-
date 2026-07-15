import { z } from 'zod';
import { prisma } from '@/lib/db';
import { handle, ok, fail, parseBody } from '@/lib/api';
import { startCollaboration } from '@/lib/orchestrator/collaboration';

export const dynamic = 'force-dynamic';

const schema = z.object({
  prompt: z.string().min(1).max(20000),
  maxIterations: z.number().int().min(0).max(10).optional(),
  maxModelCalls: z.number().int().min(2).max(100).optional(),
  idempotencyKey: z.string().optional(),
});

/**
 * Start a durable multi-agent collaboration run: independent analysis →
 * cross review → synthesis → verification → revision cycles → completion.
 * The HTTP request only initiates the run; the orchestrator advances it in
 * the background and the UI follows via SSE + persisted state.
 */
export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  return handle(async () => {
    const { id } = await params;
    const parsed = await parseBody(req, schema);
    if ('error' in parsed) return parsed.error;
    const project = await prisma.project.findUnique({ where: { id } });
    if (!project) return fail('Project not found', 404);
    const run = await startCollaboration(id, parsed.data);
    return ok(run, 202);
  });
}
