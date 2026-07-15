'use client';

import { useEffect, useState } from 'react';
import { apiCall, useApi } from '../hooks';
import { Badge, Button, Card, Field, inputCls, timeAgo } from '../ui';

interface RepoData {
  connection: {
    id: string; owner: string; repo: string; baseBranch: string; workingBranch: string;
    status: string; lastVerifiedAt: string | null; lastError: string | null;
  } | null;
  envRepo: { owner: string; repo: string } | null;
  envConfigured: boolean;
  envError: string | null;
  writablePrefixes: string[];
  protectedBranches: string[];
}

/**
 * GitHub repository configuration for a project. Credentials live only in
 * server environment variables — this panel never sees or stores them.
 */
export function GithubPanel({ projectId }: { projectId: string }) {
  const { data, loading, refresh } = useApi<RepoData>(`/api/projects/${projectId}/repo`);
  const [baseBranch, setBaseBranch] = useState('');
  const [workingBranch, setWorkingBranch] = useState('');
  const [busy, setBusy] = useState<'test' | 'save' | null>(null);
  const [msg, setMsg] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null);

  useEffect(() => {
    if (data?.connection) {
      setBaseBranch(data.connection.baseBranch);
      setWorkingBranch(data.connection.workingBranch);
    }
  }, [data?.connection?.id, data?.connection?.baseBranch, data?.connection?.workingBranch]);

  const test = async () => {
    setBusy('test');
    setMsg(null);
    const res = await apiCall(`/api/projects/${projectId}/repo/verify`, 'POST');
    setBusy(null);
    setMsg(res.ok ? { kind: 'ok', text: 'Connection verified ✓' } : { kind: 'err', text: res.error ?? 'Verification failed' });
    await refresh();
  };

  const save = async () => {
    setBusy('save');
    setMsg(null);
    const res = await apiCall(`/api/projects/${projectId}/repo`, 'PUT', { baseBranch, workingBranch });
    setBusy(null);
    setMsg(
      res.ok
        ? { kind: 'ok', text: `Saved ✓ ${baseBranch} → ${workingBranch || 'read-only (no working branch)'}` }
        : { kind: 'err', text: res.error ?? 'Save failed' },
    );
    await refresh();
  };

  if (loading || !data) return null;

  return (
    <Card className="mx-auto max-w-2xl">
      <h2 className="mb-1 text-sm font-semibold">GitHub repository</h2>
      <p className="mb-3 text-2xs text-ink-faint">
        Agents work only against the repository configured on the server (GITHUB_REPOSITORY). Reads are free once an
        agent has read access; branch creation, commits and pull requests always stop for your approval. Writes are
        only possible on branches starting with {data.writablePrefixes.join(', ')} — never on{' '}
        {data.protectedBranches.join(' or ')}.
      </p>

      {!data.envConfigured ? (
        <p className="rounded border border-amber-300 bg-amber-50 p-2 text-xs text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300">
          GitHub App is not configured on the server. Set GITHUB_APP_ID, GITHUB_INSTALLATION_ID,
          GITHUB_APP_PRIVATE_KEY_PATH and GITHUB_REPOSITORY in .env, then restart the app.
          {data.envError ? ` (${data.envError})` : ''}
        </p>
      ) : (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="font-mono font-medium">{data.envRepo?.owner}/{data.envRepo?.repo}</span>
            <Badge status={data.connection?.status ?? 'unverified'} />
            {data.connection?.lastVerifiedAt && (
              <span className="text-2xs text-ink-faint">verified {timeAgo(data.connection.lastVerifiedAt)}</span>
            )}
            <Button className="ml-auto" disabled={busy !== null} onClick={() => void test()}>
              {busy === 'test' ? 'Testing…' : 'Test connection'}
            </Button>
          </div>
          {data.connection?.lastError && data.connection.status === 'error' && (
            <p className="text-2xs text-rose-500">{data.connection.lastError}</p>
          )}
          <div className="grid grid-cols-2 gap-3">
            <Field label="Base branch (reads, diff targets, PR base)">
              <input className={inputCls} value={baseBranch} onChange={(e) => setBaseBranch(e.target.value)} placeholder="main" />
            </Field>
            <Field label={`Working branch (required for writes; must start with ${data.writablePrefixes.join(' / ')})`}>
              <input className={inputCls} value={workingBranch} onChange={(e) => setWorkingBranch(e.target.value)} placeholder="agent/my-feature" />
            </Field>
          </div>
          <p className="text-2xs text-ink-faint">
            Saved binding: {data.connection?.baseBranch ?? 'none'} → {data.connection?.workingBranch || 'read-only (no working branch)'}
          </p>
          <div className="flex items-center gap-2">
            <Button variant="primary" disabled={busy !== null} onClick={() => void save()}>
              {busy === 'save' ? 'Saving…' : 'Save'}
            </Button>
            {msg && <span className={`text-2xs ${msg.kind === 'ok' ? 'text-emerald-500' : 'text-rose-500'}`}>{msg.text}</span>}
          </div>
        </div>
      )}
    </Card>
  );
}
