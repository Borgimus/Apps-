import { z } from 'zod';
import { prisma } from '@/lib/db';
import { handle, ok, parseBody } from '@/lib/api';

export const dynamic = 'force-dynamic';

export async function GET() {
  return handle(async () => {
    const notifications = await prisma.notification.findMany({
      orderBy: { createdAt: 'desc' },
      take: 50,
    });
    const unread = await prisma.notification.count({ where: { read: false } });
    return ok({ notifications, unread });
  });
}

const schema = z.object({ ids: z.array(z.string()).optional(), markAllRead: z.boolean().optional() });

export async function PATCH(req: Request) {
  return handle(async () => {
    const parsed = await parseBody(req, schema);
    if ('error' in parsed) return parsed.error;
    if (parsed.data.markAllRead) {
      await prisma.notification.updateMany({ where: { read: false }, data: { read: true } });
    } else if (parsed.data.ids?.length) {
      await prisma.notification.updateMany({ where: { id: { in: parsed.data.ids } }, data: { read: true } });
    }
    return ok({ done: true });
  });
}
