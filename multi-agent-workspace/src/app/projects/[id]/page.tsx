'use client';

import { use, useEffect, useMemo, useState } from 'react';
import { apiCall, useApi } from '@/components/hooks';
import { ApprovalList } from '@/components/ApprovalList';
import { UsagePanel } from '@/components/UsagePanel';
import { ActivityFeed } from '@/components/project/ActivityFeed';
import { CollaborationPanel, ProjectRunRow } from '@/components/project/CollaborationPanel';
import { GithubPanel } from '@/components/project/GithubPanel';
import { FilesPanel } from '@/components/project/FilesPanel';
import { PromptInspector } from '@/components/project/PromptInspector';
import { AgentLite, TaskBoard, TaskRow } from '@/components/project/TaskBoard';
import { Badge, Button, Card, cls, EmptyState, Field, inputCls, JsonBlock, Modal, Spinner, timeAgo, usd } from '@/components/ui';

interface RunRow {
  id: string; status: string; objective: string; iterations: number; maxIterations: number;
  costUsd: number; inputTokens: number; outputTokens: number; error: string | null;
  resultSummary: string; createdAt: string;
  agent: { id: string; name: string; role: string };
  task: { id: string; title: string } | null;
}

interface ProjectDetail {
  id: string; name: string; objective: string; instructions: string; status: string;
  orchestrationMode: string; budgetUsd: number | null; updatedAt: string;
  workspace: { name: string; instructions: string };
  agents: Array<{ agent: { id: string; name: string; role: string; status: string; toolsJson: string; maxCostPerRunUsd: number; modelConfig: { name: string; provider: string; modelId: string } } }>;
  tasks: TaskRow[];
  files: Array<{ id: string; path: string; latestVersion: number; updatedAt: string }>;
  decisions: Array<{ id: string; title: string; detail: string; madeBy: string; createdAt: string }>;
  messages: Array<{ id: string; type: string; content: string; fromAgentId: string | null; toAgentId: string | null; taskId: string | null; createdAt: string }>;
  memory: Array<{ id: string; key: string; content: string; pinned: boolean }>;
  runs: RunRow[];
  projectRuns: ProjectRunRow[];
  usageTotals: { costUsd: number | null; inputTokens: number | null; outputTokens: number | null };
}

const TABS = ['Overview', 'Tasks', 'Agents', 'Activity', 'Conversations', 'Files', 'Decisions', 'Approvals', 'Usage', 'Settings'] as const;
type Tab = (typeof TABS)[number];

export default function ProjectPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data: project, loading, error, refresh } = useApi<ProjectDetail>(`/api/projects/${id}`, 4000);
  const [tab, setTab] = useState<Tab>('Overview');
  const [inspectCallId, setInspectCallId] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  // The Prompt Inspector's version buttons navigate via this window event.
  useEffect(() => {
    const handler = (e: Event) => setInspectCallId((e as CustomEvent<string>).detail);
    window.addEventListener('inspect-model-call', handler);
    return () => window.removeEventListener('inspect-model-call', handler);
  }, []);

  const agentNames = useMemo(() => {
    const map: Record<string, string> = {};
    for (const a of project?.agents ?? []) map[a.agent.id] = a.agent.name;
    return map;
  }, [project]);

  const agentsLite: AgentLite[] = (project?.agents ?? []).map((a) => ({
    id: a.agent.id, name: a.agent.name, role: a.agent.role, status: a.agent.status,
  }));

  if (loading && !project) return <Spinner label="Loading project…" />;
  if (error && !project) return <p className="p-6 text-xs text-rose-500">Error: {error}</p>;
  if (!project) return null;

  const activeRuns = project.runs.filter((r) => ['queued', 'running', 'awaiting_approval', 'paused', 'interrupted'].includes(r.status));
  const done = project.tasks.filter((t) => t.status === 'completed').length;
  const progress = project.tasks.length > 0 ? Math.round((done / project.tasks.length) * 100) : 0;
  const cost = project.usageTotals.costUsd ?? 0;
  const isDemo = project.name === 'Collaborative Software Build';

  const runDemo = async () => {
    setBanner(null);
    const res = await apiCall(`/api/projects/${id}/demo`, 'POST');
    setBanner(res.ok ? 'Demonstration started — watch the Activity tab.' : `Could not start demo: ${res.error}`);
    if (res.ok) setTab('Activity');
  };

  const pauseAll = async () => {
    const res = await apiCall(`/api/projects/${id}/pause`, 'POST');
    setBanner(res.ok ? `Paused ${(res.data as { paused: number }).paused} run(s).` : `Pause failed: ${res.error}`);
    await refresh();
  };

  return (
    <div className="flex h-full flex-col">
      {/* ---- Project header ---- */}
      <div className="border-b border-line bg-surface-raised px-6 pt-4">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-base font-semibold">{project.name}</h1>
          <Badge status={project.status} />
          <Badge status="gray" label={project.orchestrationMode} />
          <div className="flex items-center gap-1 text-2xs text-ink-faint">
            <div className="h-1.5 w-24 rounded-full bg-surface-sunken">
              <div className="h-1.5 rounded-full bg-accent" style={{ width: `${progress}%` }} />
            </div>
            {done}/{project.tasks.length} tasks
          </div>
          <span className="text-2xs text-ink-faint">
            {usd(cost)}{project.budgetUsd ? ` / ${usd(project.budgetUsd)} budget` : ''}
          </span>
          {project.budgetUsd && cost > project.budgetUsd * 0.8 && <Badge status="pending" label="budget warning" />}
          <div className="ml-auto flex items-center gap-2">
            {activeRuns.length > 0 && <Badge status="running" label={`${activeRuns.length} active run${activeRuns.length > 1 ? 's' : ''}`} />}
            {isDemo && (
              <Button variant="primary" onClick={() => void runDemo()} disabled={activeRuns.length > 0}>▶ Run demo</Button>
            )}
            <Button onClick={() => void pauseAll()} disabled={activeRuns.filter((r) => ['queued', 'running'].includes(r.status)).length === 0}>
              ⏸ Pause all
            </Button>
          </div>
        </div>
        {banner && <p className="mt-2 text-2xs text-accent">{banner}</p>}
        <nav className="mt-3 flex gap-1 overflow-x-auto">
          {TABS.map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={cls(
                'whitespace-nowrap rounded-t-md px-3 py-2 text-xs font-medium border-b-2',
                tab === t ? 'border-accent text-accent' : 'border-transparent text-ink-muted hover:text-ink',
              )}
            >
              {t}
              {t === 'Approvals' && <PendingApprovalDot projectId={id} />}
            </button>
          ))}
        </nav>
      </div>

      {/* ---- Tab body ---- */}
      <div className="flex-1 overflow-y-auto p-6">
        {tab === 'Overview' && (
          <div className="space-y-4">
            <CollaborationPanel
              projectId={id}
              runs={project.projectRuns}
              agentCount={project.agents.length}
              onChanged={() => void refresh()}
            />
            <Overview project={project} agentNames={agentNames} />
          </div>
        )}
        {tab === 'Tasks' && (
          <TaskBoard projectId={id} tasks={project.tasks} agents={agentsLite} agentNames={agentNames} onChanged={() => void refresh()} />
        )}
        {tab === 'Agents' && <AgentsTab project={project} refresh={() => void refresh()} onInspect={setInspectCallId} />}
        {tab === 'Activity' && (
          <div className="h-[calc(100vh-180px)]">
            <ActivityFeed projectId={id} agentNames={agentNames} onInspect={setInspectCallId} />
          </div>
        )}
        {tab === 'Conversations' && <Conversations project={project} agentNames={agentNames} />}
        {tab === 'Files' && <FilesPanel files={project.files} onChanged={() => void refresh()} />}
        {tab === 'Decisions' && <Decisions project={project} agentNames={agentNames} />}
        {tab === 'Approvals' && <ApprovalList projectId={id} />}
        {tab === 'Usage' && <UsagePanel projectId={id} />}
        {tab === 'Settings' && (
          <div className="space-y-4">
            <ProjectSettings project={project} refresh={() => void refresh()} />
            <GithubPanel projectId={id} />
          </div>
        )}
      </div>

      <PromptInspector modelCallId={inspectCallId} onClose={() => setInspectCallId(null)} />
    </div>
  );
}

function PendingApprovalDot({ projectId }: { projectId: string }) {
  const { data } = useApi<Array<unknown>>(`/api/approvals?status=pending&projectId=${projectId}`, 6000);
  if (!data || data.length === 0) return null;
  return <span className="ml-1 inline-block h-1.5 w-1.5 rounded-full bg-amber-500 align-middle" />;
}

// ---------------------------------------------------------------------------

function Overview({ project, agentNames }: { project: ProjectDetail; agentNames: Record<string, string> }) {
  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <Card>
        <h3 className="mb-1 text-xs font-semibold">Objective</h3>
        <p className="text-xs text-ink-muted">{project.objective || 'No objective set.'}</p>
        {project.instructions && (
          <>
            <h3 className="mb-1 mt-3 text-xs font-semibold">Instructions</h3>
            <p className="text-xs text-ink-muted">{project.instructions}</p>
          </>
        )}
      </Card>
      <Card>
        <h3 className="mb-2 text-xs font-semibold">Team</h3>
        <div className="space-y-1.5">
          {project.agents.map(({ agent }) => (
            <div key={agent.id} className="flex items-center gap-2 text-xs">
              <Badge status={agent.status} />
              <span className="font-medium">{agent.name}</span>
              <span className="text-ink-faint">{agent.role}</span>
              <span className="ml-auto text-2xs text-ink-faint">{agent.modelConfig.name}</span>
            </div>
          ))}
        </div>
      </Card>
      <Card>
        <h3 className="mb-2 text-xs font-semibold">Pinned context (shared memory)</h3>
        {project.memory.length === 0 && <p className="text-2xs text-ink-faint">Nothing pinned.</p>}
        {project.memory.map((m) => (
          <div key={m.id} className="mb-2 rounded border border-line p-2">
            <p className="text-2xs font-semibold">{m.key} {m.pinned && <span className="text-amber-500">📌</span>}</p>
            <p className="text-2xs text-ink-muted">{m.content}</p>
          </div>
        ))}
      </Card>
      <Card>
        <h3 className="mb-2 text-xs font-semibold">Recent runs</h3>
        {project.runs.length === 0 && <p className="text-2xs text-ink-faint">No runs yet.</p>}
        <div className="space-y-1">
          {project.runs.slice(0, 8).map((r) => (
            <div key={r.id} className="flex items-center gap-2 text-2xs">
              <Badge status={r.status} />
              <span className="font-medium">{agentNames[r.agent.id] ?? r.agent.name}</span>
              <span className="truncate text-ink-muted">{r.objective}</span>
              <span className="ml-auto whitespace-nowrap text-ink-faint">{usd(r.costUsd)}</span>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}

function AgentsTab({
  project,
  refresh,
  onInspect,
}: {
  project: ProjectDetail;
  refresh: () => void;
  onInspect: (id: string) => void;
}) {
  const [objectives, setObjectives] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [runDetail, setRunDetail] = useState<string | null>(null);

  const startRun = async (agentId: string) => {
    const objective = objectives[agentId]?.trim();
    if (!objective) { setErr('Enter an objective for the run.'); return; }
    setBusy(agentId);
    setErr(null);
    const res = await apiCall('/api/runs', 'POST', { agentId, projectId: project.id, objective });
    setBusy(null);
    if (!res.ok) { setErr(res.error ?? 'Failed'); return; }
    setObjectives({ ...objectives, [agentId]: '' });
    refresh();
  };

  const control = async (runId: string, action: 'pause' | 'resume' | 'cancel') => {
    if (action === 'cancel' && !window.confirm('Cancel this run?')) return;
    await apiCall(`/api/runs/${runId}/control`, 'POST', { action });
    refresh();
  };

  return (
    <div className="space-y-4">
      {err && <p className="text-xs text-rose-500">{err}</p>}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {project.agents.map(({ agent }) => {
          const runs = project.runs.filter((r) => r.agent.id === agent.id);
          const active = runs.find((r) => ['queued', 'running', 'paused', 'awaiting_approval', 'interrupted'].includes(r.status));
          return (
            <Card key={agent.id}>
              <div className="flex items-start justify-between">
                <div>
                  <p className="text-sm font-semibold">{agent.name}</p>
                  <p className="text-2xs text-ink-muted">{agent.role} · {agent.modelConfig.provider}/{agent.modelConfig.modelId}</p>
                </div>
                <Badge status={agent.status} />
              </div>
              {active ? (
                <div className="mt-2 rounded-md border border-line bg-surface-sunken p-2 text-2xs">
                  <div className="flex items-center gap-2">
                    <Badge status={active.status} />
                    <span className="truncate">{active.objective}</span>
                  </div>
                  <p className="mt-1 text-ink-faint">
                    iteration {active.iterations}/{active.maxIterations} · {usd(active.costUsd)}
                    {active.task && <> · task: {active.task.title}</>}
                  </p>
                  <div className="mt-1.5 flex gap-1.5">
                    {['queued', 'running'].includes(active.status) && <Button variant="ghost" onClick={() => void control(active.id, 'pause')}>⏸ Pause</Button>}
                    {['paused', 'interrupted'].includes(active.status) && <Button variant="ghost" onClick={() => void control(active.id, 'resume')}>▶ Resume</Button>}
                    <Button variant="danger" onClick={() => void control(active.id, 'cancel')}>⏹ Cancel</Button>
                    <Button variant="ghost" onClick={() => setRunDetail(active.id)}>Details</Button>
                  </div>
                </div>
              ) : (
                <div className="mt-2 flex gap-1.5">
                  <input
                    className={inputCls}
                    placeholder="Objective for a new run…"
                    value={objectives[agent.id] ?? ''}
                    onChange={(e) => setObjectives({ ...objectives, [agent.id]: e.target.value })}
                    onKeyDown={(e) => e.key === 'Enter' && void startRun(agent.id)}
                  />
                  <Button variant="primary" disabled={busy === agent.id} onClick={() => void startRun(agent.id)}>▶ Run</Button>
                </div>
              )}
              {runs.length > 0 && (
                <details className="mt-2">
                  <summary className="cursor-pointer text-2xs text-ink-faint">Run history ({runs.length})</summary>
                  <div className="mt-1 space-y-1">
                    {runs.slice(0, 10).map((r) => (
                      <button key={r.id} onClick={() => setRunDetail(r.id)} className="flex w-full items-center gap-2 rounded border border-line p-1.5 text-left text-2xs hover:border-accent/50">
                        <Badge status={r.status} />
                        <span className="truncate">{r.objective}</span>
                        <span className="ml-auto whitespace-nowrap text-ink-faint">{timeAgo(r.createdAt)}</span>
                      </button>
                    ))}
                  </div>
                </details>
              )}
            </Card>
          );
        })}
      </div>
      <RunDetailModal runId={runDetail} onClose={() => setRunDetail(null)} onInspect={onInspect} />
    </div>
  );
}

interface RunDetail {
  id: string; status: string; objective: string; iterations: number; error: string | null;
  resultSummary: string; costUsd: number; inputTokens: number; outputTokens: number;
  agent: { name: string; role: string };
  task: { title: string; status: string } | null;
  modelCalls: Array<{ id: string; seq: number; provider: string; modelId: string; responseText: string; status: string; costUsd: number; createdAt: string; version: number }>;
  toolCalls: Array<{ id: string; toolName: string; status: string; createdAt: string }>;
}

function RunDetailModal({ runId, onClose, onInspect }: { runId: string | null; onClose: () => void; onInspect: (id: string) => void }) {
  const { data: run, loading } = useApi<RunDetail>(runId ? `/api/runs/${runId}` : null, 3000);
  if (!runId) return null;
  return (
    <Modal open onClose={onClose} title="Run details" wide>
      {loading && !run && <Spinner />}
      {run && (
        <div className="space-y-3 text-xs">
          <div className="flex flex-wrap items-center gap-2">
            <Badge status={run.status} />
            <span className="font-medium">{run.agent.name}</span>
            <span className="text-ink-faint">{run.agent.role}</span>
            {run.task && <span className="text-ink-faint">task: {run.task.title}</span>}
            <span className="ml-auto text-2xs text-ink-faint">
              {run.iterations} iterations · {run.inputTokens + run.outputTokens} tok · {usd(run.costUsd)}
            </span>
          </div>
          <p className="text-ink-muted">{run.objective}</p>
          {run.error && <div className="rounded border border-rose-300 bg-rose-50 p-2 text-rose-700 dark:border-rose-800 dark:bg-rose-950 dark:text-rose-300">{run.error}</div>}
          {run.resultSummary && <Field label="Result"><JsonBlock value={run.resultSummary} /></Field>}
          <Field label={`Model calls (${run.modelCalls.length}) — click to inspect`}>
            <div className="space-y-1">
              {run.modelCalls.map((c) => (
                <button key={c.id} onClick={() => onInspect(c.id)} className="flex w-full items-center gap-2 rounded border border-line p-2 text-left hover:border-accent/50">
                  <span className="text-2xs text-ink-faint">#{c.seq}{c.version > 1 ? ` v${c.version}` : ''}</span>
                  <Badge status={c.status} />
                  <span className="truncate">{c.responseText || '(tool use)'}</span>
                  <span className="ml-auto whitespace-nowrap text-2xs text-ink-faint">{usd(c.costUsd)}</span>
                </button>
              ))}
            </div>
          </Field>
          {run.toolCalls.length > 0 && (
            <Field label={`Tool calls (${run.toolCalls.length})`}>
              <div className="flex flex-wrap gap-1">
                {run.toolCalls.map((t) => <Badge key={t.id} status={t.status} label={`${t.toolName}`} />)}
              </div>
            </Field>
          )}
        </div>
      )}
    </Modal>
  );
}

function Conversations({ project, agentNames }: { project: ProjectDetail; agentNames: Record<string, string> }) {
  const taskTitles = useMemo(() => {
    const m: Record<string, string> = {};
    for (const t of project.tasks) m[t.id] = t.title;
    return m;
  }, [project.tasks]);

  if (project.messages.length === 0) {
    return <EmptyState title="No messages yet" hint="Agents communicate through structured, typed messages — all of them appear here." />;
  }
  return (
    <div className="mx-auto max-w-3xl space-y-2">
      {project.messages.map((m) => (
        <div key={m.id} className="rounded-lg border border-line bg-surface-raised p-3">
          <div className="flex items-center gap-2 text-2xs">
            <span className="font-semibold">{m.fromAgentId ? agentNames[m.fromAgentId] ?? 'Agent' : 'You'}</span>
            <span className="text-ink-faint">→ {m.toAgentId ? agentNames[m.toAgentId] ?? 'Agent' : 'project'}</span>
            <Badge status={m.type} label={m.type.replace(/_/g, ' ')} />
            {m.taskId && taskTitles[m.taskId] && <span className="text-ink-faint">re: {taskTitles[m.taskId]}</span>}
            <span className="ml-auto text-ink-faint">{timeAgo(m.createdAt)}</span>
          </div>
          <p className="mt-1.5 whitespace-pre-wrap text-xs">{m.content}</p>
        </div>
      ))}
    </div>
  );
}

function Decisions({ project, agentNames }: { project: ProjectDetail; agentNames: Record<string, string> }) {
  if (project.decisions.length === 0) return <EmptyState title="No decisions recorded" hint="Agents log key choices with the record_decision tool." />;
  return (
    <div className="mx-auto max-w-3xl space-y-2">
      {project.decisions.map((d) => (
        <Card key={d.id}>
          <div className="flex items-center gap-2">
            <span aria-hidden>⚖</span>
            <p className="text-xs font-semibold">{d.title}</p>
            <span className="ml-auto text-2xs text-ink-faint">
              {d.madeBy === 'user' ? 'You' : agentNames[d.madeBy] ?? 'Agent'} · {timeAgo(d.createdAt)}
            </span>
          </div>
          {d.detail && <p className="mt-1 text-xs text-ink-muted">{d.detail}</p>}
        </Card>
      ))}
    </div>
  );
}

function ProjectSettings({ project, refresh }: { project: ProjectDetail; refresh: () => void }) {
  const [form, setForm] = useState({
    name: project.name,
    objective: project.objective,
    instructions: project.instructions,
    status: project.status,
    orchestrationMode: project.orchestrationMode,
    budgetUsd: project.budgetUsd?.toString() ?? '',
  });
  const [saved, setSaved] = useState(false);

  const save = async () => {
    await apiCall(`/api/projects/${project.id}`, 'PATCH', {
      ...form,
      budgetUsd: form.budgetUsd ? Number(form.budgetUsd) : null,
    });
    setSaved(true);
    setTimeout(() => setSaved(false), 1500);
    refresh();
  };

  return (
    <Card className="mx-auto max-w-2xl">
      <div className="space-y-3">
        <Field label="Name"><input className={inputCls} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
        <Field label="Objective"><textarea className={inputCls} rows={2} value={form.objective} onChange={(e) => setForm({ ...form, objective: e.target.value })} /></Field>
        <Field label="Instructions (included in every agent's context)">
          <textarea className={inputCls} rows={3} value={form.instructions} onChange={(e) => setForm({ ...form, instructions: e.target.value })} />
        </Field>
        <div className="grid grid-cols-3 gap-2">
          <Field label="Status">
            <select className={inputCls} value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })}>
              {['active', 'paused', 'completed', 'archived'].map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </Field>
          <Field label="Orchestration mode">
            <select className={inputCls} value={form.orchestrationMode} onChange={(e) => setForm({ ...form, orchestrationMode: e.target.value })}>
              {['manager', 'peer', 'review', 'debate', 'parallel', 'pipeline'].map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </Field>
          <Field label="Budget (USD)">
            <input className={inputCls} type="number" min={0} step={0.5} value={form.budgetUsd} onChange={(e) => setForm({ ...form, budgetUsd: e.target.value })} />
          </Field>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="primary" onClick={() => void save()}>Save</Button>
          {saved && <span className="text-2xs text-emerald-500">Saved ✓</span>}
        </div>
      </div>
    </Card>
  );
}
