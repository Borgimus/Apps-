import { prisma } from '@/lib/db';
import { ActivityEvent, bus } from '@/lib/events';
import { parseJson } from '@/lib/json';

export const dynamic = 'force-dynamic';

/**
 * Live activity stream (Server-Sent Events) for one project.
 * Sends recent history first, then streams new events from the bus.
 */
export async function GET(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const encoder = new TextEncoder();

  const history = await prisma.auditEvent.findMany({
    where: { projectId: id },
    orderBy: { createdAt: 'desc' },
    take: 100,
  });

  let cleanup: (() => void) | undefined;
  const stream = new ReadableStream({
    start(controller) {
      const send = (event: ActivityEvent) => {
        try {
          controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`));
        } catch {
          cleanup?.();
        }
      };
      for (const row of [...history].reverse()) {
        send({
          id: row.id,
          projectId: row.projectId,
          actor: row.actor,
          type: row.type,
          summary: row.summary,
          data: parseJson(row.dataJson, {}),
          refId: row.refId,
          createdAt: row.createdAt.toISOString(),
        });
      }
      const onActivity = (event: ActivityEvent) => {
        if (event.projectId === id) send(event);
      };
      bus.on('activity', onActivity);
      const heartbeat = setInterval(() => {
        try {
          controller.enqueue(encoder.encode(`: heartbeat\n\n`));
        } catch {
          cleanup?.();
        }
      }, 25_000);
      cleanup = () => {
        bus.off('activity', onActivity);
        clearInterval(heartbeat);
        try {
          controller.close();
        } catch {
          /* already closed */
        }
      };
      req.signal.addEventListener('abort', () => cleanup?.());
    },
    cancel() {
      cleanup?.();
    },
  });

  return new Response(stream, {
    headers: {
      'content-type': 'text/event-stream',
      'cache-control': 'no-cache, no-transform',
      connection: 'keep-alive',
    },
  });
}
