import { z } from 'zod';
import { handle, ok, parseBody } from '@/lib/api';
import { controlProjectRun } from '@/lib/orchestrator/collaboration';

export const dynamic = 'force-dynamic';

const schema = z.object({ action: z.enum(['pause', 'resume', 'cancel', 'retry']) });

export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  return handle(async () => {
    const { id } = await params;
    const parsed = await parseBody(req, schema);
    if ('error' in parsed) return parsed.error;
    const run = await controlProjectRun(id, parsed.data.action);
    return ok({ id: run.id, status: run.status, phase: run.phase });
  });
}
