import { prisma } from '@/lib/db';
import { ok, fail } from '@/lib/api';

export const dynamic = 'force-dynamic';

export async function GET() {
  try {
    await prisma.$queryRaw`SELECT 1`;
    return ok({ status: 'healthy', time: new Date().toISOString() });
  } catch (err) {
    return fail(`Database unavailable: ${String(err)}`, 503);
  }
}
