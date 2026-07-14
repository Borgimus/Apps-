import { z } from 'zod';
import { handle, ok, parseBody } from '@/lib/api';
import { resolveApproval } from '@/lib/orchestrator/engine';

export const dynamic = 'force-dynamic';

const schema = z.object({
  decision: z.enum(['approved', 'rejected']),
  note: z.string().max(2000).default(''),
});

export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  return handle(async () => {
    const { id } = await params;
    const parsed = await parseBody(req, schema);
    if ('error' in parsed) return parsed.error;
    await resolveApproval(id, parsed.data.decision, parsed.data.note);
    return ok({ id, status: parsed.data.decision });
  });
}
