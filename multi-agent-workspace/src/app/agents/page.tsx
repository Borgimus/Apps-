'use client';

import { useState } from 'react';
import { apiCall, useApi } from '@/components/hooks';
import { Badge, Button, Card, EmptyState, Field, inputCls, JsonBlock, Modal, Spinner, timeAgo, usd } from '@/components/ui';

interface Template { id: string; name: string; role: string; description: string; systemPrompt: string; toolsJson: string }
interface ModelConfig { id: string; name: string; provider: string; modelId: string }
interface AgentRow {
  id: string; name: string; role: string; status: string; systemPrompt: string;
  toolsJson: string; permissionsJson: string; maxCostPerRunUsd: number;
  modelConfig: ModelConfig;
  projects: Array<{ project: { id: string; name: string } }>;
  runs: Array<{ id: string; status: string; objective: string; costUsd: number; createdAt: string }>;
  metrics: { totalRuns: number; completed: number; failed: number; totalCostUsd: number };
}

export default function AgentsPage() {
  const { data: agents, loading, refresh } = useApi<AgentRow[]>('/api/agents', 8000);
  const { data: templates } = useApi<Template[]>('/api/templates');
  const { data: mc } = useApi<{ configs: ModelConfig[] }>('/api/model-configs');
  const [showCreate, setShowCreate] = useState(false);
  const [inspect, setInspect] = useState<AgentRow | null>(null);
  const [form, setForm] = useState({ name: '', templateName: '', modelConfigId: '', maxCostPerRunUsd: 1 });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const create = async () => {
    const template = templates?.find((t) => t.name === form.templateName);
    if (!template || !form.modelConfigId || !form.name) return;
    setBusy(true);
    setErr(null);
    const res = await apiCall('/api/agents', 'POST', {
      name: form.name,
      role: template.role,
      systemPrompt: template.systemPrompt,
      modelConfigId: form.modelConfigId,
      tools: JSON.parse(template.toolsJson) as string[],
      permissions: { fileWrite: true, fileWriteRequiresApproval: false },
      maxCostPerRunUsd: form.maxCostPerRunUsd,
    });
    setBusy(false);
    if (!res.ok) { setErr(res.error ?? 'Failed'); return; }
    setShowCreate(false);
    setForm({ name: '', templateName: '', modelConfigId: '', maxCostPerRunUsd: 1 });
    await refresh();
  };

  const switchModel = async (agentId: string, modelConfigId: string) => {
    await apiCall(`/api/agents/${agentId}`, 'PATCH', { modelConfigId });
    await refresh();
  };

  return (
    <div className="mx-auto max-w-6xl p-6">
      <div className="mb-5 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold">Agent roster</h1>
          <p className="text-xs text-ink-muted">Agents are model-agnostic — switch providers without rebuilding anything.</p>
        </div>
        <Button variant="primary" onClick={() => setShowCreate(true)}>+ New agent</Button>
      </div>

      {loading && <Spinner />}
      {agents && agents.length === 0 && <EmptyState title="No agents yet" hint="Create one from a template." />}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {(agents ?? []).map((a) => (
          <Card key={a.id}>
            <div className="flex items-start justify-between">
              <div>
                <p className="text-sm font-semibold">{a.name}</p>
                <p className="text-2xs text-ink-muted">{a.role}</p>
              </div>
              <Badge status={a.status} />
            </div>
            <div className="mt-2 flex items-center gap-2 text-2xs">
              <span className="text-ink-faint">Model:</span>
              <select
                className="rounded border border-line bg-surface px-1.5 py-0.5 text-2xs"
                value={a.modelConfig.id}
                onChange={(e) => void switchModel(a.id, e.target.value)}
              >
                {(mc?.configs ?? []).map((c) => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </select>
            </div>
            <div className="mt-2 flex flex-wrap gap-1">
              {(JSON.parse(a.toolsJson) as string[]).map((t) => (
                <span key={t} className="rounded bg-surface-sunken px-1.5 py-0.5 text-2xs text-ink-muted">{t}</span>
              ))}
            </div>
            <div className="mt-3 grid grid-cols-4 gap-2 border-t border-line pt-2 text-center">
              <div><p className="text-xs font-semibold">{a.metrics.totalRuns}</p><p className="text-2xs text-ink-faint">runs</p></div>
              <div><p className="text-xs font-semibold">{a.metrics.completed}</p><p className="text-2xs text-ink-faint">completed</p></div>
              <div><p className="text-xs font-semibold">{a.metrics.failed}</p><p className="text-2xs text-ink-faint">failed</p></div>
              <div><p className="text-xs font-semibold">{usd(a.metrics.totalCostUsd)}</p><p className="text-2xs text-ink-faint">cost</p></div>
            </div>
            <div className="mt-2 flex items-center justify-between">
              <p className="text-2xs text-ink-faint">
                Projects: {a.projects.map((p) => p.project.name).join(', ') || 'none'}
              </p>
              <Button variant="ghost" onClick={() => setInspect(a)}>Profile</Button>
            </div>
          </Card>
        ))}
      </div>

      <Modal open={!!inspect} onClose={() => setInspect(null)} title={inspect ? `${inspect.name} — profile` : ''} wide>
        {inspect && (
          <div className="space-y-3 text-xs">
            <div className="grid grid-cols-2 gap-3">
              <div><span className="text-ink-faint">Role:</span> {inspect.role}</div>
              <div><span className="text-ink-faint">Provider:</span> {inspect.modelConfig.provider} / {inspect.modelConfig.modelId}</div>
              <div><span className="text-ink-faint">Budget per run:</span> {usd(inspect.maxCostPerRunUsd)}</div>
              <div><span className="text-ink-faint">Status:</span> <Badge status={inspect.status} /></div>
            </div>
            <Field label="System prompt"><JsonBlock value={inspect.systemPrompt} /></Field>
            <Field label="Permissions"><JsonBlock value={JSON.parse(inspect.permissionsJson)} /></Field>
            <Field label="Recent runs">
              <div className="space-y-1">
                {inspect.runs.length === 0 && <p className="text-2xs text-ink-faint">No runs yet</p>}
                {inspect.runs.map((r) => (
                  <div key={r.id} className="flex items-center gap-2 rounded border border-line p-2">
                    <Badge status={r.status} />
                    <span className="truncate">{r.objective}</span>
                    <span className="ml-auto whitespace-nowrap text-2xs text-ink-faint">{usd(r.costUsd)} · {timeAgo(r.createdAt)}</span>
                  </div>
                ))}
              </div>
            </Field>
          </div>
        )}
      </Modal>

      <Modal open={showCreate} onClose={() => setShowCreate(false)} title="New agent from template">
        <div className="space-y-3">
          <Field label="Name">
            <input className={inputCls} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="e.g. Riley (Researcher)" />
          </Field>
          <Field label="Template">
            <select className={inputCls} value={form.templateName} onChange={(e) => setForm({ ...form, templateName: e.target.value })}>
              <option value="">Select…</option>
              {(templates ?? []).map((t) => <option key={t.id} value={t.name}>{t.name} — {t.description}</option>)}
            </select>
          </Field>
          <Field label="Model">
            <select className={inputCls} value={form.modelConfigId} onChange={(e) => setForm({ ...form, modelConfigId: e.target.value })}>
              <option value="">Select…</option>
              {(mc?.configs ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </Field>
          <Field label="Max cost per run (USD)">
            <input
              type="number" min={0.05} step={0.05} className={inputCls}
              value={form.maxCostPerRunUsd}
              onChange={(e) => setForm({ ...form, maxCostPerRunUsd: Number(e.target.value) })}
            />
          </Field>
          {err && <p className="text-xs text-rose-500">{err}</p>}
          <div className="flex justify-end gap-2">
            <Button onClick={() => setShowCreate(false)}>Cancel</Button>
            <Button variant="primary" disabled={busy || !form.name || !form.templateName || !form.modelConfigId} onClick={() => void create()}>
              {busy ? 'Creating…' : 'Create agent'}
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
