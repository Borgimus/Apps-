'use client';

import Link from 'next/link';
import { useState } from 'react';
import { apiCall, useApi } from '@/components/hooks';
import { Badge, Button, Card, EmptyState, Field, inputCls, Modal, Spinner, timeAgo, usd } from '@/components/ui';

interface ProjectSummary {
  id: string;
  name: string;
  objective: string;
  status: string;
  orchestrationMode: string;
  budgetUsd: number | null;
  updatedAt: string;
  agents: Array<{ id: string; name: string; role: string; status: string }>;
  taskCounts: { total: number; completed: number };
  pendingApprovals: number;
  activeRuns: number;
  costUsd: number;
  tokens: number;
}

export default function Dashboard() {
  const { data: projects, loading, error, refresh } = useApi<ProjectSummary[]>('/api/projects', 5000);
  const { data: agents } = useApi<Array<{ id: string; name: string; role: string }>>('/api/agents');
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState({ name: '', objective: '', orchestrationMode: 'manager', agentIds: [] as string[] });
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const create = async () => {
    setBusy(true);
    setFormError(null);
    const res = await apiCall('/api/projects', 'POST', form);
    setBusy(false);
    if (!res.ok) {
      setFormError(res.error ?? 'Failed to create project');
      return;
    }
    setShowCreate(false);
    setForm({ name: '', objective: '', orchestrationMode: 'manager', agentIds: [] });
    await refresh();
  };

  return (
    <div className="mx-auto max-w-6xl p-6">
      <div className="mb-5 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold">Projects</h1>
          <p className="text-xs text-ink-muted">Every agent action is visible, auditable and under your control.</p>
        </div>
        <Button variant="primary" onClick={() => setShowCreate(true)}>+ New project</Button>
      </div>

      {loading && <Spinner label="Loading projects…" />}
      {error && <p className="text-xs text-rose-500">Error: {error}</p>}
      {projects && projects.length === 0 && (
        <EmptyState title="No projects yet" hint="Create a project, assign agents, and give them tasks. Run `npm run setup` to seed the demonstration project." />
      )}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {(projects ?? []).map((p) => {
          const progress = p.taskCounts.total > 0 ? Math.round((p.taskCounts.completed / p.taskCounts.total) * 100) : 0;
          return (
            <Link key={p.id} href={`/projects/${p.id}`} className="block">
              <Card className="h-full hover:border-accent/60 transition-colors">
                <div className="flex items-start justify-between gap-2">
                  <h2 className="text-sm font-semibold">{p.name}</h2>
                  <Badge status={p.status} />
                </div>
                <p className="mt-1 line-clamp-2 text-xs text-ink-muted">{p.objective || 'No objective set'}</p>

                <div className="mt-3">
                  <div className="flex justify-between text-2xs text-ink-faint">
                    <span>{p.taskCounts.completed}/{p.taskCounts.total} tasks</span>
                    <span>{progress}%</span>
                  </div>
                  <div className="mt-1 h-1.5 rounded-full bg-surface-sunken">
                    <div className="h-1.5 rounded-full bg-accent" style={{ width: `${progress}%` }} />
                  </div>
                </div>

                <div className="mt-3 flex flex-wrap items-center gap-1.5">
                  {p.agents.slice(0, 5).map((a) => (
                    <span key={a.id} className="rounded-full border border-line px-2 py-0.5 text-2xs text-ink-muted" title={a.role}>
                      {a.name}
                    </span>
                  ))}
                  {p.agents.length > 5 && <span className="text-2xs text-ink-faint">+{p.agents.length - 5}</span>}
                </div>

                <div className="mt-3 flex items-center gap-3 border-t border-line pt-2 text-2xs text-ink-faint">
                  <span>{usd(p.costUsd)}</span>
                  <span>{(p.tokens / 1000).toFixed(1)}k tok</span>
                  {p.activeRuns > 0 && <Badge status="running" label={`${p.activeRuns} active`} />}
                  {p.pendingApprovals > 0 && <Badge status="pending" label={`${p.pendingApprovals} approval${p.pendingApprovals > 1 ? 's' : ''}`} />}
                  <span className="ml-auto">updated {timeAgo(p.updatedAt)}</span>
                </div>
              </Card>
            </Link>
          );
        })}
      </div>

      <Modal open={showCreate} onClose={() => setShowCreate(false)} title="New project">
        <div className="space-y-3">
          <Field label="Name">
            <input className={inputCls} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </Field>
          <Field label="Objective">
            <textarea
              className={inputCls}
              rows={3}
              value={form.objective}
              onChange={(e) => setForm({ ...form, objective: e.target.value })}
            />
          </Field>
          <Field label="Orchestration mode">
            <select
              className={inputCls}
              value={form.orchestrationMode}
              onChange={(e) => setForm({ ...form, orchestrationMode: e.target.value })}
            >
              {['manager', 'peer', 'review', 'debate', 'parallel', 'pipeline'].map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
          </Field>
          <Field label="Assign agents">
            <div className="max-h-40 space-y-1 overflow-y-auto rounded-md border border-line p-2">
              {(agents ?? []).map((a) => (
                <label key={a.id} className="flex items-center gap-2 text-xs">
                  <input
                    type="checkbox"
                    checked={form.agentIds.includes(a.id)}
                    onChange={(e) =>
                      setForm({
                        ...form,
                        agentIds: e.target.checked ? [...form.agentIds, a.id] : form.agentIds.filter((id) => id !== a.id),
                      })
                    }
                  />
                  {a.name} <span className="text-ink-faint">({a.role})</span>
                </label>
              ))}
              {(agents ?? []).length === 0 && <p className="text-2xs text-ink-faint">No agents yet — create them on the Agents page.</p>}
            </div>
          </Field>
          {formError && <p className="text-xs text-rose-500">{formError}</p>}
          <div className="flex justify-end gap-2">
            <Button onClick={() => setShowCreate(false)}>Cancel</Button>
            <Button variant="primary" disabled={busy || !form.name} onClick={() => void create()}>
              {busy ? 'Creating…' : 'Create project'}
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
