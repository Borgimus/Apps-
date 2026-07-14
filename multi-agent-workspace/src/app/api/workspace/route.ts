import { z } from 'zod';
import { prisma } from '@/lib/db';
import { handle, ok, parseBody } from '@/lib/api';

export const dynamic = 'force-dynamic';

export async function GET() {
  return handle(async () => {
    const workspace = await prisma.workspace.findFirst();
    return ok(workspace);
  });
}

const schema = z.object({
  name: z.string().min(1).optional(),
  instructions: z.string().optional(),
  dailyBudgetUsd: z.number().positive().nullable().optional(),
});

export async function PATCH(req: Request) {
  return handle(async () => {
    const parsed = await parseBody(req, schema);
    if ('error' in parsed) return parsed.error;
    const workspace = await prisma.workspace.findFirstOrThrow();
    const updated = await prisma.workspace.update({ where: { id: workspace.id }, data: parsed.data });
    return ok(updated);
  });
}
