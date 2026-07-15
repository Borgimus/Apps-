import { handle, ok } from '@/lib/api';
import { pauseAll } from '@/lib/orchestrator/engine';

export const dynamic = 'force-dynamic';

/** Pause every active run in the project. */
export async function POST(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  return handle(async () => {
    const { id } = await params;
    const paused = await pauseAll(id);
    return ok({ paused });
  });
}
