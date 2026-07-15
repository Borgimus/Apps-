'use client';

import { useEffect, useMemo, useState } from 'react';
import { apiCall, useApi } from '@/components/hooks';
import { Badge, Button, Card, Field, inputCls, Spinner } from '@/components/ui';

const READ_TOOLS = [
  'github_list_tree', 'github_read_file', 'github_search_code', 'github_read_branch',
  'github_read_pull_request', 'github_read_diff', 'github_read_checks',
];
const WRITE_TOOLS = ['github_create_branch', 'github_write_file', 'github_commit_files'];
const PR_TOOLS = ['github_open_draft_pull_request'];
const ALL_GITHUB_TOOLS = [...READ_TOOLS, ...WRITE_TOOLS, ...PR_TOOLS];

interface GitHubStatus {
  configured: boolean;
  connected: boolean;
  repository?: string;
  defaultBranch?: string;
  private?: boolean;
  permissions?: Record<string, string>;
  appId?: string;
  installationId?: string;
  error?: string;
}

interface Project {
  id: string;
  name: string;
}

interface RepositoryConnection {
  id: string;
  repositoryFullName: string;
  baseBranch: string;
  workingBranch: string;
  verifiedAt: string | null;
}

interface Agent {
  id: string;
  name: string;
  role: string;
  toolsJson: string;
  permissionsJson: string;
}

type AccessMode = 'none' | 'read' | 'write';

function accessMode(agent: Agent): AccessMode {
  const permissions = JSON.parse(agent.permissionsJson || '{}') as Record<string, boolean>;
  if (permissions.githubWrite || permissions.githubPullRequest) return 'write';
  if (permissions.githubRead) return 'read';
  return 'none';
}

export default function GitHubSettingsPage() {
  const { data: status, loading: statusLoading, refresh: refreshStatus } =
    useApi<GitHubStatus>('/api/integrations/github/status');
  const { data: projects } = useApi<Project[]>('/api/projects');
  const { data: agents, refresh: refreshAgents } = useApi<Agent[]>('/api/agents');
  const [projectId, setProjectId] = useState('');
  const { data: connection, refresh: refreshConnection } =
    useApi<RepositoryConnection | null>(projectId ? `/api/projects/${projectId}/repository` : null);
  const [baseBranch, setBaseBranch] = useState('main');
  const [workingBranch, setWorkingBranch] = useState('agent/workspace-changes');
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!projectId && projects?.[0]) setProjectId(projects[0].id);
  }, [projectId, projects]);

  useEffect(() => {
    if (connection) {
      setBaseBranch(connection.baseBranch);
      setWorkingBranch(connection.workingBranch);
    } else if (status?.defaultBranch) {
      setBaseBranch(status.defaultBranch);
    }
  }, [connection, status]);

  const selectedProject = useMemo(
    () => projects?.find((project) => project.id === projectId),
    [projectId, projects],
  );

  useEffect(() => {
    if (!connection && selectedProject) {
      const slug = selectedProject.name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
      setWorkingBranch(`agent/${slug || 'workspace-changes'}`);
    }
  }, [connection, selectedProject]);

  const connectProject = async () => {
    if (!projectId || !status?.repository) return;
    setBusy(true);
    setMessage(null);
    const result = await apiCall(`/api/projects/${projectId}/repository`, 'PATCH', {
      repositoryFullName: status.repository,
      baseBranch,
      workingBranch,
    });
    setBusy(false);
    setMessage(result.ok ? 'Project repository connection saved.' : result.error ?? 'Connection failed.');
    if (result.ok) await refreshConnection();
  };

  const setAgentAccess = async (agent: Agent, mode: AccessMode) => {
    const existingTools = JSON.parse(agent.toolsJson || '[]') as string[];
    const permissions = JSON.parse(agent.permissionsJson || '{}') as Record<string, boolean>;
    const withoutGitHub = existingTools.filter((tool) => !ALL_GITHUB_TOOLS.includes(tool));
    const tools =
      mode === 'read' ? [...withoutGitHub, ...READ_TOOLS]
      : mode === 'write' ? [...withoutGitHub, ...READ_TOOLS, ...WRITE_TOOLS, ...PR_TOOLS]
      : withoutGitHub;
    const nextPermissions = {
      ...permissions,
      githubRead: mode !== 'none',
      githubWrite: mode === 'write',
      githubPullRequest: mode === 'write',
    };

    const result = await apiCall(`/api/agents/${agent.id}`, 'PATCH', {
      tools: Array.from(new Set(tools)),
      permissions: nextPermissions,
    });
    setMessage(result.ok ? `${agent.name} now has ${mode} GitHub access.` : result.error ?? 'Update failed.');
    if (result.ok) await refreshAgents();
  };

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6">
      <div>
        <h1 className="text-lg font-semibold">GitHub integration</h1>
        <p className="text-xs text-ink-muted">
          GitHub App credentials remain server-side. Agents receive only the repository tools explicitly granted below.
        </p>
      </div>

      <Card>
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold">Connection</h2>
            <p className="mt-1 text-2xs text-ink-faint">
              Authentication uses GITHUB_APP_ID, GITHUB_INSTALLATION_ID, GITHUB_APP_PRIVATE_KEY_PATH, and GITHUB_REPOSITORY.
            </p>
          </div>
          {status && <Badge status={status.connected ? 'healthy' : 'error'} label={status.connected ? 'Connected' : 'Not connected'} />}
        </div>
        {statusLoading && <Spinner label="Testing GitHub App connection…" />}
        {status && (
          <div className="mt-3 grid grid-cols-2 gap-3 text-xs md:grid-cols-4">
            <div><p className="text-ink-faint">Repository</p><p className="font-medium">{status.repository ?? 'Unavailable'}</p></div>
            <div><p className="text-ink-faint">Default branch</p><p className="font-medium">{status.defaultBranch ?? 'Unavailable'}</p></div>
            <div><p className="text-ink-faint">App ID</p><p className="font-mono">{status.appId ?? 'Unavailable'}</p></div>
            <div><p className="text-ink-faint">Installation</p><p className="font-mono">{status.installationId ?? 'Unavailable'}</p></div>
          </div>
        )}
        {status?.error && <p className="mt-3 rounded border border-rose-300 bg-rose-50 p-2 text-xs text-rose-700 dark:border-rose-800 dark:bg-rose-950 dark:text-rose-300">{status.error}</p>}
        <div className="mt-3"><Button onClick={() => void refreshStatus()}>Test connection</Button></div>
      </Card>

      <Card>
        <h2 className="mb-1 text-sm font-semibold">Project repository</h2>
        <p className="mb-3 text-2xs text-ink-faint">
          Reads default to the base branch. Remote writes are restricted to the exact working branch and always require approval.
        </p>
        <div className="grid gap-3 md:grid-cols-3">
          <Field label="Project">
            <select className={inputCls} value={projectId} onChange={(event) => setProjectId(event.target.value)}>
              {(projects ?? []).map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
            </select>
          </Field>
          <Field label="Base branch">
            <input className={inputCls} value={baseBranch} onChange={(event) => setBaseBranch(event.target.value)} />
          </Field>
          <Field label="Working branch">
            <input className={inputCls} value={workingBranch} onChange={(event) => setWorkingBranch(event.target.value)} />
          </Field>
        </div>
        {connection?.verifiedAt && (
          <p className="mt-2 text-2xs text-ink-faint">
            Verified {new Date(connection.verifiedAt).toLocaleString()} · {connection.repositoryFullName}
          </p>
        )}
        <div className="mt-3">
          <Button
            variant="primary"
            disabled={busy || !status?.connected || !projectId || !baseBranch || !workingBranch}
            onClick={() => void connectProject()}
          >
            {busy ? 'Verifying…' : 'Connect project'}
          </Button>
        </div>
      </Card>

      <Card>
        <h2 className="mb-1 text-sm font-semibold">Agent repository access</h2>
        <p className="mb-3 text-2xs text-ink-faint">
          Read access is non-mutating. Write access only exposes approval-gated tools on the configured working branch.
        </p>
        <div className="space-y-2">
          {(agents ?? []).map((agent) => {
            const mode = accessMode(agent);
            return (
              <div key={agent.id} className="flex flex-wrap items-center gap-2 rounded border border-line p-3">
                <div className="min-w-48">
                  <p className="text-xs font-medium">{agent.name}</p>
                  <p className="text-2xs text-ink-faint">{agent.role}</p>
                </div>
                <Badge status={mode === 'none' ? 'idle' : 'active'} label={mode === 'write' ? 'Read + approved writes' : mode === 'read' ? 'Read only' : 'No GitHub access'} />
                <div className="ml-auto flex gap-1">
                  <Button variant={mode === 'read' ? 'primary' : 'default'} onClick={() => void setAgentAccess(agent, 'read')}>Read only</Button>
                  <Button variant={mode === 'write' ? 'primary' : 'default'} onClick={() => void setAgentAccess(agent, 'write')}>Read + write</Button>
                  <Button variant="danger" disabled={mode === 'none'} onClick={() => void setAgentAccess(agent, 'none')}>Revoke</Button>
                </div>
              </div>
            );
          })}
        </div>
      </Card>

      {message && <div className="rounded border border-line bg-surface-raised p-3 text-xs">{message}</div>}
    </div>
  );
}
