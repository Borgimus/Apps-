import { EventEmitter } from 'node:events';
import { prisma } from './db';
import { toJson } from './json';

/**
 * In-process event bus for live UI updates (SSE), backed by an append-only
 * AuditEvent table so history survives restarts and the UI can replay.
 */
const g = globalThis as unknown as { __bus?: EventEmitter };
export const bus: EventEmitter = g.__bus ?? new EventEmitter();
bus.setMaxListeners(200);
g.__bus = bus;

export interface ActivityEvent {
  id: string;
  projectId: string | null;
  actor: string; // "user" | "system" | agent id
  type: string;
  summary: string;
  data: unknown;
  refId?: string | null;
  createdAt: string;
}

/** Persist an audit event and broadcast it to live subscribers. */
export async function emitActivity(input: {
  projectId?: string | null;
  actor: string;
  type: string;
  summary: string;
  data?: unknown;
  refId?: string | null;
}): Promise<ActivityEvent> {
  const row = await prisma.auditEvent.create({
    data: {
      projectId: input.projectId ?? null,
      actor: input.actor,
      type: input.type,
      summary: input.summary,
      dataJson: toJson(input.data ?? {}),
      refId: input.refId ?? null,
    },
  });
  const event: ActivityEvent = {
    id: row.id,
    projectId: row.projectId,
    actor: row.actor,
    type: row.type,
    summary: row.summary,
    data: input.data ?? {},
    refId: row.refId,
    createdAt: row.createdAt.toISOString(),
  };
  bus.emit('activity', event);
  return event;
}

export async function notify(input: {
  type: string;
  title: string;
  body?: string;
  projectId?: string | null;
}): Promise<void> {
  const row = await prisma.notification.create({
    data: {
      type: input.type,
      title: input.title,
      body: input.body ?? '',
      projectId: input.projectId ?? null,
    },
  });
  bus.emit('notification', { id: row.id, ...input });
}
