'use client';

import { useState } from 'react';
import { apiCall, useApi } from './hooks';
import { Badge, Button, EmptyState, JsonBlock, Spinner, timeAgo } from './ui';

interface Approval {
  id: string;
  action: string;
  reason: string;
  payloadJson: string;
  riskLevel: string;
  status: string;
  resolution: string;
  createdAt: string;
  resolvedAt: string | null;
  project: { id: string; name: string };
  agent: { id: string; name: string; role: string } | null;
}

/** Approval queue — used both globally and inside a project workspace. */
export function ApprovalList({ projectId }: { projectId?: string }) {
  const url = projectId ? `/api/approvals?projectId=${projectId}` : '/api/approvals';
  const { data: approvals, loading, refresh } = useApi<Approval[]>(url, 5000);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);

  const resolve = async (id: string, decision: 'approved' | 'rejected') => {
    setBusy(id);
    await apiCall(`/api/approvals/${id}`, 'POST', { decision, note: notes[id] ?? '' });
    setBusy(null);
    await refresh();
  };

  if (loading) return <Spinner />;
  const pending = (approvals ?? []).filter((a) => a.status === 'pending');
  const resolved = (approvals ?? []).filter((a) => a.status !== 'pending');

  return (
    <div className="space-y-4">
      {pending.length === 0 && <EmptyState title="No pending approvals" hint="Agents will pause here when an action needs your sign-off." />}
      {pending.map((a) => (
        <div key={a.id} className="rounded-lg border border-amber-300 dark:border-amber-800 bg-surface-raised p-4">
          <div className="flex items-center gap-2">
            <Badge status="pending" />
            <Badge status={a.riskLevel === 'high' ? 'failed' : a.riskLevel === 'medium' ? 'pending' : 'ok'} label={`risk: ${a.riskLevel}`} />
            <span className="text-xs font-semibold">{a.action}</span>
            <span className="ml-auto text-2xs text-ink-faint">{timeAgo(a.createdAt)}</span>
          </div>
          <p className="mt-1 text-xs text-ink-muted">
            {a.agent ? `${a.agent.name} (${a.agent.role})` : 'System'} in <span className="font-medium">{a.project.name}</span>
          </p>
          <p className="mt-1 text-xs">{a.reason}</p>
          <details className="mt-2">
            <summary className="cursor-pointer text-2xs text-ink-faint">Inputs & payload</summary>
            <JsonBlock value={JSON.parse(a.payloadJson || '{}')} />
          </details>
          <div className="mt-3 flex items-center gap-2">
            <input
              className="flex-1 rounded-md border border-line bg-surface px-2 py-1 text-2xs"
              placeholder="Optional note (sent to the agent on reject)"
              value={notes[a.id] ?? ''}
              onChange={(e) => setNotes({ ...notes, [a.id]: e.target.value })}
            />
            <Button variant="primary" disabled={busy === a.id} onClick={() => void resolve(a.id, 'approved')}>Approve</Button>
            <Button variant="danger" disabled={busy === a.id} onClick={() => void resolve(a.id, 'rejected')}>Reject</Button>
          </div>
        </div>
      ))}

      {resolved.length > 0 && (
        <div>
          <h3 className="mb-2 text-xs font-semibold text-ink-muted">History</h3>
          <div className="space-y-1">
            {resolved.slice(0, 20).map((a) => (
              <div key={a.id} className="flex items-center gap-2 rounded border border-line bg-surface-raised px-3 py-2 text-xs">
                <Badge status={a.status} />
                <span>{a.action}</span>
                <span className="text-ink-faint">{a.agent?.name ?? 'system'} · {a.project.name}</span>
                {a.resolution && <span className="text-2xs text-ink-faint">“{a.resolution}”</span>}
                <span className="ml-auto text-2xs text-ink-faint">{a.resolvedAt ? timeAgo(a.resolvedAt) : ''}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
