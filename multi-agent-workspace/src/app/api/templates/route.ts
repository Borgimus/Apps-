import { prisma } from '@/lib/db';
import { handle, ok } from '@/lib/api';

export const dynamic = 'force-dynamic';

export async function GET() {
  return handle(async () => {
    const templates = await prisma.agentTemplate.findMany({ orderBy: { name: 'asc' } });
    return ok(templates);
  });
}
