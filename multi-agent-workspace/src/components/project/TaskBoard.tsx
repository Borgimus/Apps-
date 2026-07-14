'use client';

import { useState } from 'react';
import { apiCall } from '../hooks';
import { Badge, Button, Field, inputCls, JsonBlock, Modal, timeAgo, usd } from '../ui';

export interface TaskRow {
  id: string;
  title: string;
  description: string;
  acceptanceCriteria: string;
  status: string;
  priority: string;
  resultSummary: string;
  costUsd: number;
  createdBy: string;
  createdAt: string;
  updatedAt: string;
  ownerAgent: { id: string; name: string; role: string } | null;
  reviewerAgent: { id: string; name: string; role: string } | null;
  dependencies: Array<{ dependsOnId: string }>;
}

export interface AgentLite { id: string; name: string; role: string; status: string }

const COLUMNS: Array<{ key: string; label: string; statuses: string[] }> = [
  { key: 'backlog', label: 'Backlog', statuses: ['backlog'] },
  { key: 'ready', label: 'Ready', statuses: ['ready'] },
  { key: 'in_progress', label: 'In progress', statuses: ['in_progress'] },
  { key: 'waiting', label: 'Blocked / waiting', statuses: ['blocked', 'awaiting_review', 'awaiting_approval'] },
  { key: 'done', label: 'Done', statuses: ['completed', 'failed', 'cancelled'] },
];

const PRIORITY_MARK: Record<string, string> = { critical: '‼', high: '↑', medium: '·', low: '↓' };

export function TaskBoard({
  projectId,
  tasks,
  agents,
  agentNames,
  onChanged,
}: {
  projectId: string;
  tasks: TaskRow[];
  agents: AgentLite[];
  agentNames: Record<string, string>;
  onChanged: () => void;
}) {
  const [selected, setSelected] = useState<TaskRow | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [runObjective, setRunObjective] = useState('');
  const [runAgentId, setRunAgentId] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [form, setForm] = useState({
    title: '', description: '', acceptanceCriteria: '', priority: 'medium',
    ownerAgentId: '', reviewerAgentId: '',
  });

  const createTask = async () => {
    setBusy(true);
    setErr(null);
    const res = await apiCall('/api/tasks', 'POST', {
      projectId,
      title: form.title,
      description: form.description,
      acceptanceCriteria: form.acceptanceCriteria,
      priority: form.priority,
      ownerAgentId: form.ownerAgentId || null,
      reviewerAgentId: form.reviewerAgentId || null,
    });
    setBusy(false);
    if (!res.ok) { setErr(res.error ?? 'Failed'); return; }
    setShowCreate(false);
    setForm({ title: '', description: '', acceptanceCriteria: '', priority: 'medium', ownerAgentId: '', reviewerAgentId: '' });
    onChanged();
  };

  const setStatus = async (task: TaskRow, status: string) => {
    await apiCall(`/api/tasks/${task.id}`, 'PATCH', { status });
    setSelected(null);
    onChanged();
  };

  const startRun = async (task: TaskRow) => {
    const agentId = runAgentId || task.ownerAgent?.id;
    if (!agentId) { setErr('Choose an agent to run this task.'); return; }
    setBusy(true);
    setErr(null);
    const res = await apiCall('/api/runs', 'POST', {
      agentId,
      projectId,
      taskId: task.id,
      objective: runObjective || task.description || task.title,
    });
    setBusy(false);
    if (!res.ok) { setErr(res.error ?? 'Failed to start run'); return; }
    setSelected(null);
    setRunObjective('');
    onChanged();
  };

  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <p className="text-xs text-ink-muted">{tasks.length} tasks</p>
        <Button variant="primary" onClick={() => setShowCreate(true)}>+ New task</Button>
      </div>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-3 xl:grid-cols-5">
        {COLUMNS.map((col) => {
          const colTasks = tasks.filter((t) => col.statuses.includes(t.status));
          return (
            <div key={col.key} className="rounded-lg bg-surface-sunken p-2">
              <p className="mb-2 px-1 text-2xs font-semibold uppercase tracking-wide text-ink-faint">
                {col.label} <span className="font-normal">({colTasks.length})</span>
              </p>
              <div className="space-y-2">
                {colTasks.map((t) => (
                  <button
                    key={t.id}
                    onClick={() => { setSelected(t); setRunAgentId(t.ownerAgent?.id ?? ''); setErr(null); }}
                    className="block w-full rounded-md border border-line bg-surface-raised p-2 text-left hover:border-accent/60"
                  >
                    <p className="text-xs font-medium leading-snug">
                      <span className="mr-1 text-ink-faint" title={`priority: ${t.priority}`}>{PRIORITY_MARK[t.priority] ?? '·'}</span>
                      {t.title}
                    </p>
                    <div className="mt-1.5 flex flex-wrap items-center gap-1">
                      <Badge status={t.status} />
                      {t.ownerAgent && <span className="text-2xs text-ink-muted">{t.ownerAgent.name}</span>}
                      {t.reviewerAgent && <span className="text-2xs text-ink-faint">⟶ review: {t.reviewerAgent.name}</span>}
                    </div>
                  </button>
                ))}
                {colTasks.length === 0 && <p className="px-1 pb-1 text-2xs text-ink-faint">—</p>}
              </div>
            </div>
          );
        })}
      </div>

      <Modal open={!!selected} onClose={() => setSelected(null)} title={selected?.title ?? ''} wide>
        {selected && (
          <div className="space-y-3 text-xs">
            <div className="flex flex-wrap items-center gap-2">
              <Badge status={selected.status} />
              <Badge status="gray" label={`priority: ${selected.priority}`} />
              <span className="text-2xs text-ink-faint">cost {usd(selected.costUsd)}</span>
              <span className="text-2xs text-ink-faint">created by {selected.createdBy === 'user' ? 'you' : agentNames[selected.createdBy] ?? 'agent'} {timeAgo(selected.createdAt)}</span>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div><span className="text-ink-faint">Owner:</span> {selected.ownerAgent?.name ?? 'unassigned'}</div>
              <div><span className="text-ink-faint">Reviewer:</span> {selected.reviewerAgent?.name ?? 'none'}</div>
            </div>
            {selected.description && <Field label="Description"><JsonBlock value={selected.description} /></Field>}
            {selected.acceptanceCriteria && <Field label="Acceptance criteria"><JsonBlock value={selected.acceptanceCriteria} /></Field>}
            {selected.resultSummary && <Field label="Latest result"><JsonBlock value={selected.resultSummary} /></Field>}

            <div className="rounded-md border border-line p-3">
              <p className="mb-2 font-medium">Run an agent on this task</p>
              <div className="grid grid-cols-2 gap-2">
                <Field label="Agent">
                  <select className={inputCls} value={runAgentId} onChange={(e) => setRunAgentId(e.target.value)}>
                    <option value="">Select…</option>
                    {agents.map((a) => <option key={a.id} value={a.id}>{a.name} ({a.role})</option>)}
                  </select>
                </Field>
                <Field label="Objective (defaults to the task description)">
                  <input className={inputCls} value={runObjective} onChange={(e) => setRunObjective(e.target.value)} />
                </Field>
              </div>
              {err && <p className="mt-1 text-rose-500">{err}</p>}
              <div className="mt-2">
                <Button variant="primary" disabled={busy} onClick={() => void startRun(selected)}>
                  {busy ? 'Starting…' : '▶ Start run'}
                </Button>
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-2 border-t border-line pt-3">
              <span className="text-2xs text-ink-faint">Move to:</span>
              {['backlog', 'ready', 'in_progress', 'blocked', 'awaiting_review', 'completed', 'cancelled']
                .filter((s) => s !== selected.status)
                .map((s) => (
                  <Button key={s} variant="ghost" onClick={() => void setStatus(selected, s)}>{s.replace(/_/g, ' ')}</Button>
                ))}
            </div>
          </div>
        )}
      </Modal>

      <Modal open={showCreate} onClose={() => setShowCreate(false)} title="New task">
        <div className="space-y-3">
          <Field label="Title"><input className={inputCls} value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></Field>
          <Field label="Description"><textarea className={inputCls} rows={3} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></Field>
          <Field label="Acceptance criteria"><textarea className={inputCls} rows={2} value={form.acceptanceCriteria} onChange={(e) => setForm({ ...form, acceptanceCriteria: e.target.value })} /></Field>
          <div className="grid grid-cols-3 gap-2">
            <Field label="Priority">
              <select className={inputCls} value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })}>
                {['low', 'medium', 'high', 'critical'].map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
            </Field>
            <Field label="Owner">
              <select className={inputCls} value={form.ownerAgentId} onChange={(e) => setForm({ ...form, ownerAgentId: e.target.value })}>
                <option value="">unassigned</option>
                {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
              </select>
            </Field>
            <Field label="Reviewer">
              <select className={inputCls} value={form.reviewerAgentId} onChange={(e) => setForm({ ...form, reviewerAgentId: e.target.value })}>
                <option value="">none</option>
                {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
              </select>
            </Field>
          </div>
          {err && <p className="text-xs text-rose-500">{err}</p>}
          <div className="flex justify-end gap-2">
            <Button onClick={() => setShowCreate(false)}>Cancel</Button>
            <Button variant="primary" disabled={busy || !form.title} onClick={() => void createTask()}>{busy ? 'Creating…' : 'Create task'}</Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
