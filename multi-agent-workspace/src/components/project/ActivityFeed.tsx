'use client';

import { useEffect, useRef, useState } from 'react';
import { LiveEvent, useLiveEvents } from '../hooks';
import { Badge, cls, EmptyState, JsonBlock, statusColor } from '../ui';

const TYPE_ICONS: Record<string, string> = {
  model_call: '🧠',
  tool_call: '🔧',
  run_started: '▶',
  run_completed: '✔',
  run_cancelled: '⏹',
  run_queued: '…',
  task_created: '☐',
  status_change: '⇄',
  message: '✉',
  approval: '🔏',
  file_change: '📄',
  decision: '⚖',
  error: '✖',
  retry: '↻',
  demo_step: '★',
  project_created: '◆',
  project_completed: '🏁',
  model_call_rerun: '⟲',
};

/**
 * Live activity timeline: streams every prompt, response, tool call, task
 * change, file edit, approval and error over SSE. Events expand for detail;
 * model calls open in the Prompt Inspector.
 */
export function ActivityFeed({
  projectId,
  agentNames,
  onInspect,
}: {
  projectId: string;
  agentNames: Record<string, string>;
  onInspect: (modelCallId: string) => void;
}) {
  const events = useLiveEvents(projectId);
  const [filter, setFilter] = useState<string>('all');
  const [follow, setFollow] = useState(true);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (follow) bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [events.length, follow]);

  const types = Array.from(new Set(events.map((e) => e.type))).sort();
  const visible = filter === 'all' ? events : events.filter((e) => e.type === filter);

  return (
    <div className="flex h-full flex-col">
      <div className="mb-2 flex items-center gap-2">
        <select
          className="rounded-md border border-line bg-surface px-2 py-1 text-2xs"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        >
          <option value="all">All events ({events.length})</option>
          {types.map((t) => (
            <option key={t} value={t}>{t.replace(/_/g, ' ')} ({events.filter((e) => e.type === t).length})</option>
          ))}
        </select>
        <label className="ml-auto flex items-center gap-1 text-2xs text-ink-muted">
          <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> Follow live
        </label>
        <span className="flex items-center gap-1 text-2xs text-emerald-500">
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-500" /> streaming
        </span>
      </div>

      <div className="flex-1 space-y-1 overflow-y-auto pr-1">
        {visible.length === 0 && <EmptyState title="No activity yet" hint="Start a run or the demo to see live events." />}
        {visible.map((e) => (
          <EventRow key={e.id} event={e} agentNames={agentNames} onInspect={onInspect} />
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}

function EventRow({
  event,
  agentNames,
  onInspect,
}: {
  event: LiveEvent;
  agentNames: Record<string, string>;
  onInspect: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const actor = event.actor === 'user' ? 'You' : event.actor === 'system' ? 'System' : agentNames[event.actor] ?? 'Agent';
  const modelCallId =
    event.type === 'model_call' || event.type === 'model_call_rerun'
      ? ((event.data as { modelCallId?: string }).modelCallId ?? event.refId)
      : null;
  const color = statusColor(
    event.type === 'error' ? 'error' : event.type === 'run_completed' ? 'completed' : event.type === 'approval' ? 'pending' : 'gray',
  );

  return (
    <div
      className={cls(
        'rounded-md border border-line bg-surface-raised px-3 py-2',
        color === 'red' && 'border-rose-300 dark:border-rose-800',
      )}
    >
      <button className="flex w-full items-center gap-2 text-left" onClick={() => setOpen(!open)}>
        <span className="w-5 text-center" aria-hidden>{TYPE_ICONS[event.type] ?? '•'}</span>
        <span className="text-2xs font-medium text-ink-muted whitespace-nowrap">{actor}</span>
        <span className="truncate text-xs">{event.summary}</span>
        <span className="ml-auto whitespace-nowrap text-2xs text-ink-faint">
          {new Date(event.createdAt).toLocaleTimeString()}
        </span>
        <span className="text-2xs text-ink-faint">{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <div className="mt-2 border-t border-line pt-2">
          <div className="mb-1 flex items-center gap-2">
            <Badge status="gray" label={event.type.replace(/_/g, ' ')} />
            {modelCallId && (
              <button className="text-2xs font-medium text-accent hover:underline" onClick={() => onInspect(modelCallId)}>
                Open in prompt inspector →
              </button>
            )}
          </div>
          <JsonBlock value={event.data} />
        </div>
      )}
    </div>
  );
}
