'use client';

import { useState } from 'react';
import { apiCall, useApi } from '../hooks';
import { Badge, Button, Card, Field, inputCls, JsonBlock, Modal, Spinner, timeAgo, usd } from '../ui';

export interface ProjectRunRow {
  id: string;
  prompt: string;
  status: string;
  phase: string;
  iteration: number;
  maxIterations: number;
  finalOutput: string;
  failureReason: string | null;
  createdAt: string;
  completedAt: string | null;
}

const PHASE_LABELS: Record<string, string> = {
  independent_analysis: '1/5 Independent analysis',
  cross_review: '2/5 Cross review',
  synthesis: '3/5 Synthesis',
  verification: '4/5 Verification',
  revision: '4/5 Revision',
  finalizing: '5/5 Finalizing',
  done: 'Done',
};

/**
 * Start and monitor durable multi-agent collaboration runs. The panel only
 * initiates runs and renders persisted state — orchestration happens in the
 * backend and survives refreshes and restarts.
 */
export function CollaborationPanel({
  projectId,
  runs,
  agentCount,
  onChanged,
}: {
  projectId: string;
  runs: ProjectRunRow[];
  agentCount: number;
  onChanged: () => void;
}) {
  const [prompt, setPrompt] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [detailId, setDetailId] = useState<string | null>(null);

  const start = async () => {
    if (!prompt.trim()) return;
    setBusy(true);
    setErr(null);
    const res = await apiCall(`/api/projects/${projectId}/collaborate`, 'POST', { prompt: prompt.trim() });
    setBusy(false);
    if (!res.ok) { setErr(res.error ?? 'Failed to start'); return; }
    setPrompt('');
    onChanged();
  };

  const control = async (id: string, action: string) => {
    if (action === 'cancel' && !window.confirm('Cancel this collaboration run?')) return;
    await apiCall(`/api/project-runs/${id}/control`, 'POST', { action });
    onChanged();
  };

  return (
    <Card>
      <h3 className="mb-1 text-xs font-semibold">Collaboration</h3>
      <p className="mb-2 text-2xs text-ink-faint">
        Submit one prompt — two agents respond independently, cross-review each other, synthesize a combined
        result, verify it, and revise until approved. Every step appears in the Activity tab.
      </p>
      {agentCount < 2 ? (
        <p className="text-2xs text-amber-500">Assign at least 2 agents to enable collaboration runs.</p>
      ) : (
        <div className="flex gap-1.5">
          <textarea
            className={inputCls}
            rows={2}
            placeholder="Project prompt, e.g. “Design a rollout plan for feature X”…"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
          />
          <Button variant="primary" disabled={busy || !prompt.trim()} onClick={() => void start()}>
            {busy ? 'Starting…' : '▶ Collaborate'}
          </Button>
        </div>
      )}
      {err && <p className="mt-1 text-2xs text-rose-500">{err}</p>}

      <div className="mt-3 space-y-2">
        {runs.map((r) => (
          <div key={r.id} className="rounded-md border border-line p-2.5">
            <div className="flex flex-wrap items-center gap-2">
              <Badge status={r.status} />
              <Badge status="gray" label={PHASE_LABELS[r.phase] ?? r.phase} />
              {r.iteration > 0 && <span className="text-2xs text-ink-faint">revision {r.iteration}/{r.maxIterations}</span>}
              <span className="truncate text-xs">{r.prompt}</span>
              <span className="ml-auto whitespace-nowrap text-2xs text-ink-faint">{timeAgo(r.createdAt)}</span>
            </div>
            {r.failureReason && <p className="mt-1 text-2xs text-rose-500">{r.failureReason}</p>}
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {['created', 'running'].includes(r.status) && <Button variant="ghost" onClick={() => void control(r.id, 'pause')}>⏸ Pause</Button>}
              {r.status === 'paused' && <Button variant="ghost" onClick={() => void control(r.id, 'resume')}>▶ Resume</Button>}
              {['failed', 'paused'].includes(r.status) && <Button variant="ghost" onClick={() => void control(r.id, 'retry')}>↻ Retry</Button>}
              {!['completed', 'cancelled'].includes(r.status) && <Button variant="danger" onClick={() => void control(r.id, 'cancel')}>⏹ Cancel</Button>}
              <Button variant="ghost" onClick={() => setDetailId(r.id)}>Steps</Button>
              {r.status === 'completed' && r.finalOutput && (
                <details className="w-full">
                  <summary className="cursor-pointer text-2xs font-medium text-emerald-500">Final result</summary>
                  <JsonBlock value={r.finalOutput} />
                </details>
              )}
            </div>
          </div>
        ))}
      </div>
      <ProjectRunDetail runId={detailId} onClose={() => setDetailId(null)} />
    </Card>
  );
}

interface RunDetail {
  status: string; phase: string; iteration: number; prompt: string; finalOutput: string;
  failureReason: string | null;
  roles: { agentA: { name: string } | null; agentB: { name: string } | null; synthesizer: { name: string } | null };
  steps: Record<string, string>;
  agentRuns: Array<{
    id: string; runType: string; status: string; resultSummary: string; error: string | null;
    parsedOutputJson: string | null; costUsd: number; createdAt: string;
    agent: { name: string; role: string };
    modelCalls: Array<{ id: string }>;
  }>;
}

function ProjectRunDetail({ runId, onClose }: { runId: string | null; onClose: () => void }) {
  const { data, loading } = useApi<RunDetail>(runId ? `/api/project-runs/${runId}` : null, 3000);
  if (!runId) return null;
  return (
    <Modal open onClose={onClose} title="Collaboration steps" wide>
      {loading && !data && <Spinner />}
      {data && (
        <div className="space-y-3 text-xs">
          <div className="flex flex-wrap items-center gap-2">
            <Badge status={data.status} />
            <Badge status="gray" label={data.phase.replace(/_/g, ' ')} />
            <span className="text-2xs text-ink-faint">
              A: {data.roles.agentA?.name} · B: {data.roles.agentB?.name} · synthesizer: {data.roles.synthesizer?.name}
            </span>
          </div>
          <p className="text-ink-muted">{data.prompt}</p>
          {data.failureReason && <p className="text-rose-500">{data.failureReason}</p>}
          <div className="space-y-1.5">
            {data.agentRuns.map((s) => (
              <details key={s.id} className="rounded border border-line p-2">
                <summary className="flex cursor-pointer items-center gap-2">
                  <Badge status={s.status} />
                  <Badge status="gray" label={s.runType} />
                  <span className="font-medium">{s.agent.name}</span>
                  <span className="truncate text-ink-muted">{s.resultSummary || s.error || ''}</span>
                  <span className="ml-auto whitespace-nowrap text-2xs text-ink-faint">
                    {s.modelCalls.length} call{s.modelCalls.length === 1 ? '' : 's'} · {usd(s.costUsd)}
                  </span>
                </summary>
                {s.error && <p className="mt-1 text-rose-500">{s.error}</p>}
                {s.parsedOutputJson && (
                  <div className="mt-2">
                    <Field label="Structured output"><JsonBlock value={JSON.parse(s.parsedOutputJson)} /></Field>
                  </div>
                )}
                {s.modelCalls.length > 0 && (
                  <button
                    className="mt-1 text-2xs font-medium text-accent hover:underline"
                    onClick={() => window.dispatchEvent(new CustomEvent('inspect-model-call', { detail: s.modelCalls[0]!.id }))}
                  >
                    Open prompt in inspector →
                  </button>
                )}
              </details>
            ))}
          </div>
          {data.finalOutput && <Field label="Final output"><JsonBlock value={data.finalOutput} /></Field>}
        </div>
      )}
    </Modal>
  );
}
