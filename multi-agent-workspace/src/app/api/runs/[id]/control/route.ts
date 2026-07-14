import { z } from 'zod';
import { handle, ok, parseBody } from '@/lib/api';
import { cancelRun, pauseRun, resumeRun } from '@/lib/orchestrator/engine';

export const dynamic = 'force-dynamic';

const schema = z.object({ action: z.enum(['pause', 'resume', 'cancel']) });

export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  return handle(async () => {
    const { id } = await params;
    const parsed = await parseBody(req, schema);
    if ('error' in parsed) return parsed.error;
    const run =
      parsed.data.action === 'pause' ? await pauseRun(id)
      : parsed.data.action === 'resume' ? await resumeRun(id)
      : await cancelRun(id);
    return ok({ id: run.id, status: run.status });
  });
}
