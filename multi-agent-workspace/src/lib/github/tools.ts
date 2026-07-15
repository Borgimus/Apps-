import { prisma } from '../db';
import { getAllowedRepo, ghRequest, GithubError } from './auth';

/**
 * GitHub tool implementations. Every call is invoked exclusively through the
 * centralized tool executor (src/lib/tools/execute.ts), which handles
 * allowlist checks, zod validation, audit records, and approval gating.
 *
 * Hard safety rules enforced here (defense in depth, independent of
 * permissions and approvals):
 *  - Writes only to branches prefixed agent/, agents/ or feature/
 *  - Protected branches can never be written, whatever their prefix
 *  - Workflow/CI definitions and git internals can never be modified
 *  - No merge, no branch deletion, no admin, no secret operations exist
 */

export const PROTECTED_BRANCHES = new Set([
  'main',
  'master',
  'claude/options-trading-research-system-TIU0p',
]);

export const WRITABLE_PREFIXES = ['agent/', 'agents/', 'feature/'];

export const GITHUB_READ_TOOLS = new Set([
  'github_list_tree',
  'github_read_file',
  'github_search_code',
  'github_read_branch',
  'github_read_pull_request',
  'github_read_diff',
  'github_read_checks',
]);

export const GITHUB_WRITE_TOOLS = new Set([
  'github_create_branch',
  'github_write_file',
  'github_commit_files',
  'github_open_draft_pull_request',
]);

export function assertWritableBranch(branch: string): void {
  const b = branch.trim();
  if (PROTECTED_BRANCHES.has(b)) {
    throw new GithubError(`Branch "${b}" is protected — agents may never write to it`, 'forbidden_target');
  }
  if (!WRITABLE_PREFIXES.some((p) => b.startsWith(p))) {
    throw new GithubError(
      `Agents may only write to branches starting with ${WRITABLE_PREFIXES.join(', ')} (got "${b}")`,
      'forbidden_target',
    );
  }
}

export function assertWritablePath(path: string): void {
  const norm = path.replace(/^\/+/, '');
  if (norm.includes('..') || norm.startsWith('.git/')) {
    throw new GithubError(`Illegal repository path: ${norm}`, 'forbidden_target');
  }
  if (norm.startsWith('.github/workflows') || norm.startsWith('.github/actions')) {
    throw new GithubError('Modifying workflows or actions is prohibited', 'forbidden_target');
  }
}

const MAX_FILE_BYTES = 200_000;
const MAX_DIFF_CHARS = 100_000;
const MAX_TREE_ENTRIES = 500;

async function defaultBaseBranch(projectId: string): Promise<string> {
  const conn = await prisma.repoConnection.findUnique({ where: { projectId } });
  if (conn?.baseBranch) return conn.baseBranch;
  const { owner, repo } = getAllowedRepo();
  const data = await ghRequest<{ default_branch?: string }>(`/repos/${owner}/${repo}`);
  return data.default_branch ?? 'main';
}

/** Execute one GitHub tool. Returns JSON-safe output for the tool result. */
export async function runGithubTool(
  projectId: string,
  toolName: string,
  input: Record<string, unknown>,
): Promise<unknown> {
  const { owner, repo } = getAllowedRepo();
  const repoPath = `/repos/${owner}/${repo}`;

  switch (toolName) {
    case 'github_list_tree': {
      const ref = String(input.ref ?? (await defaultBaseBranch(projectId)));
      const branch = await ghRequest<{ commit: { sha: string } }>(`${repoPath}/branches/${encodeURIComponent(ref)}`);
      const tree = await ghRequest<{ tree: Array<{ path: string; type: string; size?: number }>; truncated: boolean }>(
        `${repoPath}/git/trees/${branch.commit.sha}?recursive=1`,
      );
      const prefix = typeof input.path === 'string' ? input.path.replace(/^\/+/, '') : '';
      const entries = tree.tree
        .filter((e) => (prefix ? e.path.startsWith(prefix) : true))
        .slice(0, MAX_TREE_ENTRIES)
        .map((e) => ({ path: e.path, type: e.type, size: e.size ?? null }));
      return { ref, entries, truncated: tree.truncated || entries.length === MAX_TREE_ENTRIES };
    }

    case 'github_read_file': {
      const path = String(input.path).replace(/^\/+/, '');
      const ref = input.ref ? `?ref=${encodeURIComponent(String(input.ref))}` : '';
      const data = await ghRequest<{ content?: string; encoding?: string; size?: number; sha?: string }>(
        `${repoPath}/contents/${path.split('/').map(encodeURIComponent).join('/')}${ref}`,
      );
      if (data.size !== undefined && data.size > MAX_FILE_BYTES) {
        return { path, size: data.size, content: null, note: `File exceeds the ${MAX_FILE_BYTES}-byte read limit` };
      }
      const content = data.content && data.encoding === 'base64' ? Buffer.from(data.content, 'base64').toString('utf-8') : '';
      return { path, sha: data.sha ?? null, size: data.size ?? content.length, content };
    }

    case 'github_search_code': {
      const q = `${String(input.query)} repo:${owner}/${repo}`;
      const data = await ghRequest<{ total_count: number; items?: Array<{ path: string; name: string }> }>(
        `/search/code?q=${encodeURIComponent(q)}&per_page=20`,
      );
      return { totalCount: data.total_count, results: (data.items ?? []).map((i) => ({ path: i.path, name: i.name })) };
    }

    case 'github_read_branch': {
      const branch = String(input.branch);
      const data = await ghRequest<{ name: string; commit: { sha: string; commit?: { message?: string; author?: { date?: string } } }; protected?: boolean }>(
        `${repoPath}/branches/${encodeURIComponent(branch)}`,
      );
      return {
        name: data.name,
        headSha: data.commit.sha,
        lastCommitMessage: data.commit.commit?.message?.split('\n')[0] ?? '',
        lastCommitAt: data.commit.commit?.author?.date ?? null,
        protected: data.protected ?? false,
        writableByAgents: !PROTECTED_BRANCHES.has(data.name) && WRITABLE_PREFIXES.some((p) => data.name.startsWith(p)),
      };
    }

    case 'github_read_pull_request': {
      const n = Number(input.number);
      const pr = await ghRequest<{
        number: number; title: string; body: string | null; state: string; draft: boolean;
        merged: boolean; head: { ref: string; sha: string }; base: { ref: string };
        additions: number; deletions: number; changed_files: number; html_url: string;
      }>(`${repoPath}/pulls/${n}`);
      return {
        number: pr.number, title: pr.title, body: pr.body ?? '', state: pr.state, draft: pr.draft,
        merged: pr.merged, headBranch: pr.head.ref, headSha: pr.head.sha, baseBranch: pr.base.ref,
        additions: pr.additions, deletions: pr.deletions, changedFiles: pr.changed_files, url: pr.html_url,
      };
    }

    case 'github_read_diff': {
      let diff: string;
      if (input.number !== undefined) {
        diff = await ghRequest<string>(`${repoPath}/pulls/${Number(input.number)}`, { accept: 'application/vnd.github.diff', raw: true });
      } else {
        const base = encodeURIComponent(String(input.base ?? (await defaultBaseBranch(projectId))));
        const head = encodeURIComponent(String(input.head));
        diff = await ghRequest<string>(`${repoPath}/compare/${base}...${head}`, { accept: 'application/vnd.github.diff', raw: true });
      }
      const truncated = diff.length > MAX_DIFF_CHARS;
      return { diff: truncated ? diff.slice(0, MAX_DIFF_CHARS) : diff, truncated };
    }

    case 'github_read_checks': {
      const ref = encodeURIComponent(String(input.ref));
      const data = await ghRequest<{ total_count: number; check_runs?: Array<{ name: string; status: string; conclusion: string | null; html_url?: string }> }>(
        `${repoPath}/commits/${ref}/check-runs`,
      );
      return {
        totalCount: data.total_count,
        checks: (data.check_runs ?? []).map((c) => ({ name: c.name, status: c.status, conclusion: c.conclusion })),
      };
    }

    case 'github_create_branch': {
      const branch = String(input.branch);
      assertWritableBranch(branch);
      const from = String(input.fromBranch ?? (await defaultBaseBranch(projectId)));
      const base = await ghRequest<{ object: { sha: string } }>(`${repoPath}/git/ref/heads/${encodeURIComponent(from)}`);
      await ghRequest(`${repoPath}/git/refs`, {
        method: 'POST',
        body: { ref: `refs/heads/${branch}`, sha: base.object.sha },
      });
      return { branch, from, sha: base.object.sha };
    }

    case 'github_write_file': {
      const branch = String(input.branch);
      const path = String(input.path).replace(/^\/+/, '');
      assertWritableBranch(branch);
      assertWritablePath(path);
      const encPath = path.split('/').map(encodeURIComponent).join('/');
      let existingSha: string | undefined;
      try {
        const existing = await ghRequest<{ sha?: string }>(`${repoPath}/contents/${encPath}?ref=${encodeURIComponent(branch)}`);
        existingSha = existing.sha;
      } catch (err) {
        if (!(err instanceof GithubError && err.code === 'not_found')) throw err; // new file is fine
      }
      const result = await ghRequest<{ commit?: { sha?: string } }>(`${repoPath}/contents/${encPath}`, {
        method: 'PUT',
        body: {
          message: String(input.message ?? `Update ${path} via agent`),
          content: Buffer.from(String(input.content), 'utf-8').toString('base64'),
          branch,
          ...(existingSha ? { sha: existingSha } : {}),
        },
      });
      return { branch, path, commitSha: result.commit?.sha ?? null, updated: Boolean(existingSha) };
    }

    case 'github_commit_files': {
      const branch = String(input.branch);
      assertWritableBranch(branch);
      const files = input.files as Array<{ path: string; content: string }>;
      for (const f of files) assertWritablePath(f.path);
      const ref = await ghRequest<{ object: { sha: string } }>(`${repoPath}/git/ref/heads/${encodeURIComponent(branch)}`);
      const headCommit = await ghRequest<{ tree: { sha: string } }>(`${repoPath}/git/commits/${ref.object.sha}`);
      const tree = await ghRequest<{ sha: string }>(`${repoPath}/git/trees`, {
        method: 'POST',
        body: {
          base_tree: headCommit.tree.sha,
          tree: files.map((f) => ({ path: f.path.replace(/^\/+/, ''), mode: '100644', type: 'blob', content: f.content })),
        },
      });
      const commit = await ghRequest<{ sha: string }>(`${repoPath}/git/commits`, {
        method: 'POST',
        body: { message: String(input.message), tree: tree.sha, parents: [ref.object.sha] },
      });
      await ghRequest(`${repoPath}/git/refs/heads/${encodeURIComponent(branch)}`, {
        method: 'PATCH',
        body: { sha: commit.sha, force: false },
      });
      return { branch, commitSha: commit.sha, files: files.map((f) => f.path) };
    }

    case 'github_open_draft_pull_request': {
      const head = String(input.head);
      assertWritableBranch(head); // PRs may only come FROM agent branches
      const base = String(input.base ?? (await defaultBaseBranch(projectId)));
      if (WRITABLE_PREFIXES.some((p) => base.startsWith(p)) && base === head) {
        throw new GithubError('head and base cannot be the same branch', 'forbidden_target');
      }
      const pr = await ghRequest<{ number: number; html_url: string }>(`${repoPath}/pulls`, {
        method: 'POST',
        body: {
          title: String(input.title),
          body: String(input.body ?? ''),
          head,
          base,
          draft: true, // agents can only open DRAFT PRs; merging is human-only
        },
      });
      return { number: pr.number, url: pr.html_url, head, base, draft: true };
    }

    default:
      throw new GithubError(`Unknown GitHub tool: ${toolName}`, 'api_error');
  }
}
