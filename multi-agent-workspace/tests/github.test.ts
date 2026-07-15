import { generateKeyPairSync } from 'node:crypto';
import { mkdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { prisma } from '@/lib/db';
import { clearTokenCache, getAllowedRepo, ghRequest, GithubError, redactSecretsFromText, verifyConnection } from '@/lib/github/auth';
import { assertWritableBranch, assertWritablePath } from '@/lib/github/tools';
import { toolNeedsApproval } from '@/lib/tools/execute';
import { resolveApproval } from '@/lib/orchestrator/engine';
import { assembleContext } from '@/lib/orchestrator/prompt';
import { executeTool, ToolContext } from '@/lib/tools/execute';
import { makeFixture } from './helpers';

/**
 * GitHub integration tests. ALL network access is mocked — these tests can
 * never read from or write to a real repository.
 */

const KEY_PATH = path.resolve(__dirname, 'tmp/test-github-key.pem');

function setEnv() {
  process.env.GITHUB_APP_ID = '12345';
  process.env.GITHUB_INSTALLATION_ID = '67890';
  process.env.GITHUB_APP_PRIVATE_KEY_PATH = KEY_PATH;
  process.env.GITHUB_REPOSITORY = 'Borgimus/Apps-';
}

function clearEnv() {
  delete process.env.GITHUB_APP_ID;
  delete process.env.GITHUB_INSTALLATION_ID;
  delete process.env.GITHUB_APP_PRIVATE_KEY_PATH;
  delete process.env.GITHUB_REPOSITORY;
}

/** fetch stub: token endpoint + a route table; records every request. */
function stubGithub(routes: Record<string, (init?: RequestInit) => { status?: number; body?: unknown; text?: string }>) {
  const calls: Array<{ url: string; method: string; body: unknown; authorization: string | null }> = [];
  vi.stubGlobal('fetch', vi.fn(async (url: unknown, init?: RequestInit) => {
    const u = String(url);
    const method = init?.method ?? 'GET';
    calls.push({
      url: u,
      method,
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
      authorization: new Headers(init?.headers).get('authorization'),
    });
    if (u.includes('/app/installations/')) {
      return new Response(JSON.stringify({ token: 'ghs_mocktoken1234567890abcd', expires_at: new Date(Date.now() + 3600_000).toISOString() }), { status: 201 });
    }
    for (const [route, handler] of Object.entries(routes)) {
      if (u.includes(route)) {
        const r = handler(init);
        return new Response(r.text ?? JSON.stringify(r.body ?? {}), { status: r.status ?? 200 });
      }
    }
    return new Response(JSON.stringify({ message: 'unmocked route' }), { status: 404 });
  }));
  return calls;
}

beforeEach(() => {
  mkdirSync(path.dirname(KEY_PATH), { recursive: true });
  const { privateKey } = generateKeyPairSync('rsa', { modulusLength: 2048 });
  writeFileSync(KEY_PATH, privateKey.export({ type: 'pkcs1', format: 'pem' }));
  setEnv();
  clearTokenCache();
});

afterEach(() => {
  vi.unstubAllGlobals();
  clearEnv();
  clearTokenCache();
});

describe('github auth service', () => {
  it('fails closed when configuration is missing', async () => {
    clearEnv();
    expect(() => getAllowedRepo()).toThrow(GithubError);
    await expect(ghRequest('/repos/Borgimus/Apps-')).rejects.toMatchObject({ code: 'not_configured' });
  });

  it('mints a repo-restricted installation token and caches it', async () => {
    const calls = stubGithub({ '/repos/Borgimus/Apps-': () => ({ body: { full_name: 'Borgimus/Apps-', default_branch: 'main', private: true } }) });
    await ghRequest('/repos/Borgimus/Apps-');
    await ghRequest('/repos/Borgimus/Apps-');
    const tokenCalls = calls.filter((c) => c.url.includes('/app/installations/'));
    expect(tokenCalls).toHaveLength(1); // cached after first mint
    expect(tokenCalls[0]!.body).toEqual({ repositories: ['Apps-'] }); // restricted to the one repo
    // The JWT goes only to the token endpoint; API calls use the installation token.
    expect(tokenCalls[0]!.url).toContain('/app/installations/67890/access_tokens');
    const jwt = tokenCalls[0]!.authorization?.replace(/^Bearer\s+/, '');
    expect(jwt).toBeTruthy();
    const payload = JSON.parse(
      Buffer.from(jwt!.split('.')[1]!, 'base64url').toString('utf8'),
    ) as { iat: number; exp: number };
    const now = Math.floor(Date.now() / 1000);
    expect(payload.exp - now).toBeLessThanOrEqual(540);
    expect(payload.exp - payload.iat).toBe(600);
  });

  it('refuses any request outside the allowed repository without touching the network', async () => {
    const calls = stubGithub({});
    await expect(ghRequest('/repos/someone-else/other-repo/contents/x')).rejects.toMatchObject({ code: 'repo_mismatch' });
    await expect(ghRequest('/user')).rejects.toMatchObject({ code: 'repo_mismatch' });
    expect(calls).toHaveLength(0); // fails closed before any fetch
  });

  it('verifies repository identity and fails closed on mismatch', async () => {
    stubGithub({ '/repos/Borgimus/Apps-': () => ({ body: { full_name: 'Attacker/Evil', default_branch: 'main' } }) });
    await expect(verifyConnection()).rejects.toMatchObject({ code: 'repo_mismatch' });
  });

  it('redacts key material and tokens from error text', () => {
    const dirty = 'oops -----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY----- and ghs_abcdefghijklmnop123 and Bearer eyJhbGciOi.something';
    const clean = redactSecretsFromText(dirty);
    expect(clean).not.toContain('BEGIN RSA');
    expect(clean).not.toContain('ghs_');
    expect(clean).not.toContain('eyJhbGciOi');
  });
});

describe('branch and path guards', () => {
  it('refuses protected branches and non-agent prefixes', () => {
    for (const b of ['main', 'master', 'claude/options-trading-research-system-TIU0p', 'develop', 'release/v1']) {
      expect(() => assertWritableBranch(b), b).toThrow(GithubError);
    }
    for (const b of ['agent/fix-bug', 'agents/team-x', 'feature/dashboard']) {
      expect(() => assertWritableBranch(b), b).not.toThrow();
    }
    expect(() => assertWritableBranch('agent/other', 'agent/configured')).toThrow('configured working branch');
    expect(() => assertWritableBranch('agent/other', '')).toThrow('Configure a project working branch');
  });

  it('refuses workflow, action and traversal paths', () => {
    for (const p of ['.github/workflows/deploy.yml', '.github/actions/x/action.yml', '../etc/passwd', '.git', '.git/config']) {
      expect(() => assertWritablePath(p), p).toThrow(GithubError);
    }
    expect(() => assertWritablePath('src/app.py')).not.toThrow();
  });
});

describe('approval policy', () => {
  it('always requires approval for GitHub mutations, never for reads', () => {
    const fullPerms = { githubRead: true, githubWrite: true, githubPullRequest: true };
    for (const t of ['github_create_branch', 'github_write_file', 'github_commit_files', 'github_open_draft_pull_request']) {
      expect(toolNeedsApproval(t, fullPerms), t).toBe(true); // permissions cannot bypass approval
    }
    for (const t of ['github_read_file', 'github_list_tree', 'github_read_diff', 'github_read_checks']) {
      expect(toolNeedsApproval(t, fullPerms), t).toBe(false);
    }
  });
});

describe('GitHub approval prompt guidance', () => {
  it('includes the verified project repository binding in every agent prompt', async () => {
    const f = await makeFixture();
    await prisma.repoConnection.create({
      data: {
        projectId: f.project.id,
        owner: 'Borgimus',
        repo: 'Apps-',
        baseBranch: 'main',
        workingBranch: 'agent/github-reconcile-smoke-test',
        status: 'connected',
        lastVerifiedAt: new Date(),
      },
    });

    const assembled = await assembleContext({
      workspace: f.workspace,
      project: f.project,
      agent: { ...f.agent, modelConfig: f.modelConfig },
      task: null,
      objective: 'Run a GitHub smoke test',
    });

    expect(assembled.system).toContain('Repository: Borgimus/Apps-');
    expect(assembled.system).toContain('Base branch: main');
    expect(assembled.system).toContain('Working branch: agent/github-reconcile-smoke-test');
    expect(assembled.system).toContain('never call request_approval separately for a GitHub mutation');
    expect(assembled.contextManifest.repositoryConnection).toEqual({
      owner: 'Borgimus',
      repo: 'Apps-',
      baseBranch: 'main',
      workingBranch: 'agent/github-reconcile-smoke-test',
      status: 'connected',
    });
  });

  it('tells models not to create duplicate approval requests', async () => {
    const { TOOL_SPECS } = await import('@/lib/tools/defs');
    for (const name of [
      'github_create_branch',
      'github_write_file',
      'github_commit_files',
      'github_open_draft_pull_request',
    ]) {
      expect(TOOL_SPECS[name]!.description).toContain('do not call request_approval separately');
    }
  });
});

describe('permissioned execution through the central executor', () => {
  async function makeCtx(
    permissions: Record<string, boolean>,
    tools: string[],
    workingBranch = 'agent/docs-update',
  ): Promise<ToolContext> {
    const f = await makeFixture({ tools, permissions });
    const run = await prisma.agentRun.create({
      data: { projectId: f.project.id, agentId: f.agent.id, objective: 'github test', status: 'running' },
    });
    await prisma.repoConnection.create({
      data: {
        projectId: f.project.id,
        owner: 'Borgimus',
        repo: 'Apps-',
        baseBranch: 'main',
        workingBranch,
        status: 'connected',
        lastVerifiedAt: new Date(),
      },
    });
    return {
      projectId: f.project.id,
      agentId: f.agent.id,
      agentName: f.agent.name,
      runId: run.id,
      allowedTools: tools,
    };
  }

  it('does not let an unrelated approval resolve a pending GitHub tool gate', async () => {
    const ctx = await makeCtx(
      { githubRead: true, githubWrite: true },
      ['github_create_branch'],
    );
    await prisma.agentRun.update({
      where: { id: ctx.runId },
      data: {
        status: 'awaiting_approval',
        pendingToolCallJson: JSON.stringify({
          modelCallId: 'model-call-test',
          current: {
            id: 'call-create-branch',
            name: 'github_create_branch',
            input: { branch: 'agent/docs-update', fromBranch: 'main' },
          },
          remaining: [],
        }),
      },
    });
    const unrelated = await prisma.approvalRequest.create({
      data: {
        projectId: ctx.projectId,
        runId: ctx.runId,
        agentId: ctx.agentId,
        action: 'Create a new branch',
        reason: 'Redundant agent-created approval',
      },
    });

    await resolveApproval(unrelated.id, 'rejected', 'Automatic tool gate handles this');

    const run = await prisma.agentRun.findUniqueOrThrow({ where: { id: ctx.runId } });
    expect(run.status).toBe('awaiting_approval');
    expect(run.pendingToolCallJson).toContain('github_create_branch');
  });

  it('denies GitHub reads without the githubRead permission and audits the attempt', async () => {
    const ctx = await makeCtx({ githubRead: false }, ['github_read_file']);
    const calls = stubGithub({});
    const result = await executeTool(ctx, 'github_read_file', { path: 'README.md' });
    expect(result.ok).toBe(false);
    expect(result.error).toContain('githubRead');
    expect(calls).toHaveLength(0); // denied before any network access
    const audit = await prisma.toolCall.findFirst({ where: { runId: ctx.runId, toolName: 'github_read_file' } });
    expect(audit).not.toBeNull(); // visible in history even when denied
  });

  it('requires a verified project repository connection before any network call', async () => {
    const ctx = await makeCtx({ githubRead: true }, ['github_read_file']);
    await prisma.repoConnection.delete({ where: { projectId: ctx.projectId } });
    const calls = stubGithub({});
    const result = await executeTool(ctx, 'github_read_file', { path: 'README.md' });
    expect(result.ok).toBe(false);
    expect(result.error).toContain('verified GitHub repository connection');
    expect(calls).toHaveLength(0);
  });

  it('executes reads with githubRead and records the audit trail', async () => {
    const ctx = await makeCtx({ githubRead: true }, ['github_read_file']);
    stubGithub({
      '/contents/README.md': () => ({ body: { content: Buffer.from('# Hello').toString('base64'), encoding: 'base64', size: 7, sha: 'abc' } }),
    });
    const result = await executeTool(ctx, 'github_read_file', { path: 'README.md', ref: 'main' });
    expect(result.ok).toBe(true);
    expect((result.output as { content: string }).content).toBe('# Hello');
    const audit = await prisma.toolCall.findFirst({ where: { runId: ctx.runId, toolName: 'github_read_file', status: 'ok' } });
    expect(audit).not.toBeNull();
    const event = await prisma.auditEvent.findFirst({ where: { projectId: ctx.projectId, type: 'tool_call' } });
    expect(event?.summary).toContain('github_read_file'); // visible in the Activity timeline
  });

  it('refuses writes to protected branches before any network call', async () => {
    const ctx = await makeCtx({ githubRead: true, githubWrite: true }, ['github_write_file']);
    const calls = stubGithub({});
    for (const branch of ['main', 'claude/options-trading-research-system-TIU0p', 'develop']) {
      const result = await executeTool(ctx, 'github_write_file', { branch, path: 'x.txt', content: 'x' });
      expect(result.ok, branch).toBe(false);
    }
    expect(calls).toHaveLength(0);
  });

  it('refuses workflow modification even on an agent branch', async () => {
    const ctx = await makeCtx({ githubRead: true, githubWrite: true }, ['github_write_file'], 'agent/sneaky');
    const calls = stubGithub({});
    const result = await executeTool(ctx, 'github_write_file', {
      branch: 'agent/sneaky', path: '.github/workflows/evil.yml', content: 'oops',
    });
    expect(result.ok).toBe(false);
    expect(result.error).toContain('prohibited');
    expect(calls).toHaveLength(0);
  });

  it('refuses writes outside the project configured working branch before any network call', async () => {
    const ctx = await makeCtx({ githubRead: true, githubWrite: true }, ['github_write_file']);
    const calls = stubGithub({});
    const result = await executeTool(ctx, 'github_write_file', {
      branch: 'agent/other', path: 'x.txt', content: 'x',
    });
    expect(result.ok).toBe(false);
    expect(result.error).toContain('configured working branch');
    expect(calls).toHaveLength(0);
  });

  it('refuses oversized write payloads before any network call', async () => {
    const ctx = await makeCtx({ githubRead: true, githubWrite: true }, ['github_write_file']);
    const calls = stubGithub({});
    const result = await executeTool(ctx, 'github_write_file', {
      branch: 'agent/docs-update', path: 'large.txt', content: 'x'.repeat(500_001),
    });
    expect(result.ok).toBe(false);
    expect(calls).toHaveLength(0);
  });

  it('writes to the configured agent branch when permitted (this is the post-approval execution path)', async () => {
    const ctx = await makeCtx({ githubRead: true, githubWrite: true }, ['github_write_file']);
    const calls = stubGithub({
      '/contents/docs/note.md': (init) =>
        (init?.method ?? 'GET') === 'PUT'
          ? { body: { commit: { sha: 'newsha' } } }
          : { status: 404, body: { message: 'Not Found' } },
    });
    const result = await executeTool(ctx, 'github_write_file', {
      branch: 'agent/docs-update', path: 'docs/note.md', content: 'hello', message: 'Add note',
    });
    expect(result.ok).toBe(true);
    const put = calls.find((c) => c.method === 'PUT');
    expect(put?.body).toMatchObject({ branch: 'agent/docs-update', message: 'Add note' });
  });

  it('opens pull requests as DRAFT only, from agent branches only, gated by githubPullRequest', async () => {
    const noPr = await makeCtx(
      { githubRead: true, githubWrite: true, githubPullRequest: false },
      ['github_open_draft_pull_request'],
      'agent/x',
    );
    stubGithub({});
    const denied = await executeTool(noPr, 'github_open_draft_pull_request', { head: 'agent/x', title: 'T' });
    expect(denied.ok).toBe(false);
    expect(denied.error).toContain('githubPullRequest');

    const ctx = await makeCtx(
      { githubRead: true, githubWrite: true, githubPullRequest: true },
      ['github_open_draft_pull_request'],
      'agent/x',
    );
    const calls = stubGithub({ '/pulls': () => ({ body: { number: 42, html_url: 'https://example.test/pr/42' } }) });
    const fromMain = await executeTool(ctx, 'github_open_draft_pull_request', { head: 'main', title: 'nope' });
    expect(fromMain.ok).toBe(false); // PRs must come FROM agent branches
    const wrongBase = await executeTool(ctx, 'github_open_draft_pull_request', {
      head: 'agent/x', title: 'wrong base', base: 'develop',
    });
    expect(wrongBase.ok).toBe(false); // PRs must target the configured base branch

    const result = await executeTool(ctx, 'github_open_draft_pull_request', { head: 'agent/x', title: 'My PR', base: 'main' });
    expect(result.ok).toBe(true);
    const post = calls.find((c) => c.url.endsWith('/pulls') && c.method === 'POST');
    expect(post?.body).toMatchObject({ draft: true, head: 'agent/x', base: 'main' }); // always draft — merging is human-only
  });

  it('exposes no merge, delete or admin tools', async () => {
    const { ALL_TOOL_NAMES } = await import('@/lib/tools/defs');
    const forbidden = ALL_TOOL_NAMES.filter((t) => /merge|delete_branch|admin|secret|workflow/.test(t));
    expect(forbidden).toEqual([]);
  });
});
