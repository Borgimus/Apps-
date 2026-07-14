'use client';

import { useEffect, useState } from 'react';
import { apiCall, useApi } from '../hooks';
import { Badge, Button, Field, inputCls, JsonBlock, Modal, Spinner, usd } from '../ui';

interface ModelCallDetail {
  id: string;
  runId: string | null;
  provider: string;
  modelId: string;
  systemPrompt: string;
  messagesJson: string;
  toolDefsJson: string;
  settingsJson: string;
  contextJson: string;
  responseText: string;
  toolCallsJson: string;
  stopReason: string;
  inputTokens: number;
  outputTokens: number;
  costUsd: number;
  durationMs: number;
  status: string;
  error: string | null;
  version: number;
  parentCallId: string | null;
  createdAt: string;
  agent: { id: string; name: string; role: string } | null;
  versions: Array<{ id: string; version: number; createdAt: string; status: string }>;
  toolCalls: Array<{ id: string; toolName: string; inputJson: string; outputJson: string; status: string; durationMs: number }>;
}

/**
 * Prompt Inspector: the full, exact record of one model interaction —
 * system prompt, messages, tool definitions, settings, context manifest,
 * response, usage and cost. Supports edit + rerun; reruns create new
 * versions and never overwrite history.
 */
export function PromptInspector({ modelCallId, onClose }: { modelCallId: string | null; onClose: () => void }) {
  const { data: call, loading, refresh } = useApi<ModelCallDetail>(modelCallId ? `/api/model-calls/${modelCallId}` : null);
  const [editMode, setEditMode] = useState(false);
  const [editSystem, setEditSystem] = useState('');
  const [editTemp, setEditTemp] = useState(0.3);
  const [rerunning, setRerunning] = useState(false);
  const [rerunId, setRerunId] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (call) {
      setEditSystem(call.systemPrompt);
      const settings = JSON.parse(call.settingsJson || '{}') as { temperature?: number };
      setEditTemp(settings.temperature ?? 0.3);
      setEditMode(false);
      setRerunId(null);
      setErr(null);
    }
  }, [call?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!modelCallId) return null;

  const doRerun = async () => {
    setRerunning(true);
    setErr(null);
    const res = await apiCall(`/api/model-calls/${modelCallId}/rerun`, 'POST', {
      ...(editMode ? { systemPrompt: editSystem, temperature: editTemp } : {}),
    });
    setRerunning(false);
    if (!res.ok) { setErr(res.error ?? 'Rerun failed'); return; }
    const created = res.data as { id: string };
    setRerunId(created.id);
    await refresh();
  };

  const viewing = rerunId ?? modelCallId;

  return (
    <Modal open onClose={onClose} title="Prompt inspector" wide>
      {loading && <Spinner />}
      {call && (
        <div className="space-y-3 text-xs">
          <div className="flex flex-wrap items-center gap-2">
            <Badge status={call.status} />
            <span className="font-mono">{call.provider}/{call.modelId}</span>
            {call.agent && <span className="text-ink-muted">{call.agent.name} ({call.agent.role})</span>}
            <span className="text-ink-faint">v{call.version}</span>
            <span className="ml-auto text-2xs text-ink-faint">
              {call.inputTokens} in / {call.outputTokens} out · {usd(call.costUsd)} · {call.durationMs}ms · {new Date(call.createdAt).toLocaleString()}
            </span>
          </div>

          {call.versions.length > 1 && (
            <div className="flex items-center gap-1 text-2xs">
              <span className="text-ink-faint">Versions:</span>
              {call.versions.map((v) => (
                <button
                  key={v.id}
                  onClick={() => { setRerunId(null); window.dispatchEvent(new CustomEvent('inspect-model-call', { detail: v.id })); }}
                  className={`rounded px-1.5 py-0.5 ${v.id === viewing ? 'bg-accent text-white' : 'bg-surface-sunken text-ink-muted hover:text-ink'}`}
                >
                  v{v.version}
                </button>
              ))}
            </div>
          )}

          {call.error && (
            <div className="rounded border border-rose-300 bg-rose-50 p-2 text-rose-700 dark:border-rose-800 dark:bg-rose-950 dark:text-rose-300">
              {call.error}
            </div>
          )}

          <Field label="System prompt (includes workspace, project and agent instructions)">
            {editMode ? (
              <textarea className={inputCls} rows={10} value={editSystem} onChange={(e) => setEditSystem(e.target.value)} />
            ) : (
              <JsonBlock value={call.systemPrompt} />
            )}
          </Field>

          <details open>
            <summary className="cursor-pointer text-2xs font-medium text-ink-muted">Messages sent to the model</summary>
            <div className="mt-1 space-y-1">
              {(JSON.parse(call.messagesJson || '[]') as Array<{ role: string; content?: string; name?: string; toolCalls?: unknown[] }>).map((m, i) => (
                <div key={i} className="rounded border border-line p-2">
                  <Badge status={m.role === 'user' ? 'queued' : m.role === 'assistant' ? 'running' : 'gray'} label={m.role + (m.name ? `:${m.name}` : '')} />
                  <pre className="code-block mt-1">{m.content ?? ''}</pre>
                  {m.toolCalls && m.toolCalls.length > 0 && <JsonBlock value={m.toolCalls} />}
                </div>
              ))}
            </div>
          </details>

          <details>
            <summary className="cursor-pointer text-2xs font-medium text-ink-muted">Tool definitions ({(JSON.parse(call.toolDefsJson || '[]') as unknown[]).length})</summary>
            <JsonBlock value={JSON.parse(call.toolDefsJson || '[]')} />
          </details>

          <details>
            <summary className="cursor-pointer text-2xs font-medium text-ink-muted">Context manifest (what was included and why)</summary>
            <JsonBlock value={JSON.parse(call.contextJson || '{}')} />
          </details>

          <details>
            <summary className="cursor-pointer text-2xs font-medium text-ink-muted">Settings</summary>
            {editMode ? (
              <Field label="Temperature">
                <input type="number" step={0.1} min={0} max={2} className={inputCls} value={editTemp} onChange={(e) => setEditTemp(Number(e.target.value))} />
              </Field>
            ) : (
              <JsonBlock value={JSON.parse(call.settingsJson || '{}')} />
            )}
          </details>

          <Field label="Response">
            <JsonBlock value={call.responseText || '(no text — tool use only)'} />
          </Field>

          {(JSON.parse(call.toolCallsJson || '[]') as unknown[]).length > 0 && (
            <Field label="Tool calls requested by the model">
              <JsonBlock value={JSON.parse(call.toolCallsJson)} />
            </Field>
          )}

          {call.toolCalls.length > 0 && (
            <Field label="Executed tool calls">
              <div className="space-y-1">
                {call.toolCalls.map((tc) => (
                  <details key={tc.id} className="rounded border border-line p-2">
                    <summary className="cursor-pointer">
                      <Badge status={tc.status} label={`${tc.toolName} (${tc.status})`} /> <span className="text-2xs text-ink-faint">{tc.durationMs}ms</span>
                    </summary>
                    <p className="mt-1 text-2xs text-ink-faint">Input</p>
                    <JsonBlock value={JSON.parse(tc.inputJson || 'null')} />
                    <p className="mt-1 text-2xs text-ink-faint">Output</p>
                    <JsonBlock value={JSON.parse(tc.outputJson || 'null')} />
                  </details>
                ))}
              </div>
            </Field>
          )}

          {err && <p className="text-rose-500">{err}</p>}
          {rerunId && <p className="text-emerald-500">Rerun created as a new version — open it from the versions row above.</p>}

          <div className="flex justify-end gap-2 border-t border-line pt-3">
            <Button onClick={() => setEditMode(!editMode)}>{editMode ? 'View original' : 'Edit prompt'}</Button>
            <Button variant="primary" disabled={rerunning} onClick={() => void doRerun()}>
              {rerunning ? 'Running…' : editMode ? 'Rerun with edits' : 'Duplicate & rerun'}
            </Button>
          </div>
          <p className="text-2xs text-ink-faint">
            Reruns are recorded as new versions and never overwrite the original. Rerun responses are inspection-only — tool calls they request are not executed.
          </p>
        </div>
      )}
    </Modal>
  );
}
