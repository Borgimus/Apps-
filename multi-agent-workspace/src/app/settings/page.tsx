'use client';

import { useEffect, useState } from 'react';
import { apiCall, useApi } from '@/components/hooks';
import { Button, Card, Field, inputCls, Spinner } from '@/components/ui';

interface Workspace { id: string; name: string; instructions: string; dailyBudgetUsd: number | null }
interface ModelConfig {
  id: string; name: string; provider: string; modelId: string; baseUrl: string | null;
  apiKeyEnvVar: string | null; temperature: number; maxTokens: number;
  inputPricePerMTok: number; outputPricePerMTok: number;
}

export default function SettingsPage() {
  const { data: workspace, loading, refresh } = useApi<Workspace>('/api/workspace');
  const { data: mc, refresh: refreshMc } = useApi<{ configs: ModelConfig[]; providers: string[] }>('/api/model-configs');
  const [form, setForm] = useState({ name: '', instructions: '', dailyBudgetUsd: '' });
  const [saved, setSaved] = useState(false);
  const [newModel, setNewModel] = useState({
    name: '', provider: 'openai-compatible', modelId: '', baseUrl: '', apiKeyEnvVar: '',
    inputPricePerMTok: 0, outputPricePerMTok: 0,
  });
  const [modelErr, setModelErr] = useState<string | null>(null);

  useEffect(() => {
    if (workspace) {
      setForm({
        name: workspace.name,
        instructions: workspace.instructions,
        dailyBudgetUsd: workspace.dailyBudgetUsd?.toString() ?? '',
      });
    }
  }, [workspace]);

  const save = async () => {
    await apiCall('/api/workspace', 'PATCH', {
      name: form.name,
      instructions: form.instructions,
      dailyBudgetUsd: form.dailyBudgetUsd ? Number(form.dailyBudgetUsd) : null,
    });
    setSaved(true);
    setTimeout(() => setSaved(false), 1500);
    await refresh();
  };

  const addModel = async () => {
    setModelErr(null);
    const res = await apiCall('/api/model-configs', 'POST', {
      ...newModel,
      baseUrl: newModel.baseUrl || null,
      apiKeyEnvVar: newModel.apiKeyEnvVar || null,
    });
    if (!res.ok) { setModelErr(res.error ?? 'Failed'); return; }
    setNewModel({ name: '', provider: 'openai-compatible', modelId: '', baseUrl: '', apiKeyEnvVar: '', inputPricePerMTok: 0, outputPricePerMTok: 0 });
    await refreshMc();
  };

  if (loading) return <Spinner />;

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6">
      <div>
        <h1 className="text-lg font-semibold">Settings</h1>
        <p className="text-xs text-ink-muted">Single-user local mode. Provider API keys are read from environment variables only.</p>
      </div>

      <Card>
        <h2 className="mb-3 text-sm font-semibold">Workspace</h2>
        <div className="space-y-3">
          <Field label="Name">
            <input className={inputCls} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </Field>
          <Field label="Global instructions (prepended to every agent's context)">
            <textarea className={inputCls} rows={3} value={form.instructions} onChange={(e) => setForm({ ...form, instructions: e.target.value })} />
          </Field>
          <Field label="Daily budget (USD, blank = unlimited)">
            <input className={inputCls} type="number" min={0} step={1} value={form.dailyBudgetUsd} onChange={(e) => setForm({ ...form, dailyBudgetUsd: e.target.value })} />
          </Field>
          <div className="flex items-center gap-2">
            <Button variant="primary" onClick={() => void save()}>Save</Button>
            {saved && <span className="text-2xs text-emerald-500">Saved ✓</span>}
          </div>
        </div>
      </Card>

      <Card>
        <h2 className="mb-1 text-sm font-semibold">Model configurations</h2>
        <p className="mb-3 text-2xs text-ink-faint">
          Models are configured separately from agents; an agent can be switched between any of these at any time.
          Keys are referenced by environment-variable name and never stored in the database.
        </p>
        <div className="mb-4 overflow-x-auto">
          <table className="w-full text-2xs">
            <thead>
              <tr className="border-b border-line text-left text-ink-faint">
                <th className="py-1 pr-2 font-medium">Name</th>
                <th className="py-1 pr-2 font-medium">Provider</th>
                <th className="py-1 pr-2 font-medium">Model ID</th>
                <th className="py-1 pr-2 font-medium">Endpoint</th>
                <th className="py-1 pr-2 font-medium">Key env var</th>
                <th className="py-1 font-medium text-right">$/MTok in·out</th>
              </tr>
            </thead>
            <tbody>
              {(mc?.configs ?? []).map((c) => (
                <tr key={c.id} className="border-b border-line/40 last:border-0">
                  <td className="py-1.5 pr-2 font-medium">{c.name}</td>
                  <td className="py-1.5 pr-2">{c.provider}</td>
                  <td className="py-1.5 pr-2 font-mono">{c.modelId}</td>
                  <td className="py-1.5 pr-2 text-ink-faint">{c.baseUrl ?? 'default'}</td>
                  <td className="py-1.5 pr-2 font-mono">{c.apiKeyEnvVar ?? '—'}</td>
                  <td className="py-1.5 text-right">{c.inputPricePerMTok} · {c.outputPricePerMTok}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <h3 className="mb-2 text-xs font-semibold">Add model configuration</h3>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
          <Field label="Display name"><input className={inputCls} value={newModel.name} onChange={(e) => setNewModel({ ...newModel, name: e.target.value })} /></Field>
          <Field label="Provider">
            <select className={inputCls} value={newModel.provider} onChange={(e) => setNewModel({ ...newModel, provider: e.target.value })}>
              {(mc?.providers ?? ['anthropic', 'openai-compatible', 'mock']).map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </Field>
          <Field label="Model ID"><input className={inputCls} value={newModel.modelId} onChange={(e) => setNewModel({ ...newModel, modelId: e.target.value })} placeholder="e.g. claude-sonnet-5" /></Field>
          <Field label="Base URL (openai-compatible only)"><input className={inputCls} value={newModel.baseUrl} onChange={(e) => setNewModel({ ...newModel, baseUrl: e.target.value })} placeholder="https://…/v1" /></Field>
          <Field label="API key env var"><input className={inputCls} value={newModel.apiKeyEnvVar} onChange={(e) => setNewModel({ ...newModel, apiKeyEnvVar: e.target.value })} placeholder="MY_PROVIDER_KEY" /></Field>
          <div className="grid grid-cols-2 gap-2">
            <Field label="$/MTok in"><input className={inputCls} type="number" min={0} value={newModel.inputPricePerMTok} onChange={(e) => setNewModel({ ...newModel, inputPricePerMTok: Number(e.target.value) })} /></Field>
            <Field label="$/MTok out"><input className={inputCls} type="number" min={0} value={newModel.outputPricePerMTok} onChange={(e) => setNewModel({ ...newModel, outputPricePerMTok: Number(e.target.value) })} /></Field>
          </div>
        </div>
        {modelErr && <p className="mt-2 text-xs text-rose-500">{modelErr}</p>}
        <div className="mt-3">
          <Button variant="primary" disabled={!newModel.name || !newModel.modelId} onClick={() => void addModel()}>Add model</Button>
        </div>
      </Card>
    </div>
  );
}
