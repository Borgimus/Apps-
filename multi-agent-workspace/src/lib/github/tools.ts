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

export function assertWritableBranch(branch: string, configuredWorkingBranch?: string | null): void {
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
  if (configuredWorkingBranch && b !== configuredWorkingBranch) {
    throw new GithubError(
      `Writes are restricted to the project's configured working branch: ${configuredWorkingBranch}`,
      'forbidden_target',
    );
  }
}

export function assertWritablePath(path: string): void {
  const norm = path.replace(/^\/+/, '');
  if (norm.includes('..') || norm === '.git' || norm.startsWith('.git/')) {
    throw new GithubError(`Illegal repository path: ${norm}`, 'forbidden_target');
  }
  if (norm.startsWith('.github/workflows') || norm.startsWith('.github/actions')) {
    throw new GithubError('Modifying workflows or actions is prohibited', 'forbidden_target');
  }
}

const MAX_FILE_BYTES = 200_000;
const MAX_WRITE_FILE_BYTES = 500_000;
const MAX_COMMIT_FILES = 20;
const MAX_COMMIT_BYTES = 1_000_000;
const MAX_DIFF_CHARS = 100_000;
const MAX_TREE_ENTRIES = 500;

async function connectionFor(projectId: string) {
  const connection = await prisma.repoConnection.findUnique({ where: { projectId } });
  if (!connection || connection.status !== 'connected') {
    throw new GithubError('This project does not have a verified GitHub repository connection', 'not_configured');
  }
  const allowed = getAllowedRepo();
  if (
    connection.owner.toLowerCase() !== allowed.owner.toLowerCase() ||
    connection.repo.toLowerCase() !== allowed.repo.toLowerCase()
  ) {
    throw new GithubError('Project repository connection does not match GITHUB_REPOSITORY', 'repo_mismatch');
  }
  return connection;
}

function byteLength(content: string): number {
  return Buffer.byteLength(content, 'utf8');
}

/** Execute one GitHub tool. Returns JSON-safe output for the tool result. */
export async function runGithubTool(
  projectId: string,
  toolName: string,
  input: Record<string, unknown>,
): Promise<unknown> {
  const { owner, repo } = getAllowedRepo();
  const connection = await connectionFor(projectId);
  const repoPath = `/repos/${owner}/${repo}`;

  switch (toolName) {
    case 'github_list_tree': {
      const ref = String(input.ref ?? connection.baseBranch);
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
      const ref = `?ref=${encodeURIComponent(String(input.ref ?? connection.baseBranch))}`;
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
        const base = encodeURIComponent(String(input.base ?? connection.baseBranch));
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
      assertWritableBranch(branch, connection.workingBranch);
      const from = String(input.fromBranch ?? connection.baseBranch);
      if (from !== connection.baseBranch) {
        throw new GithubError(`Branches may only be created from the configured base branch: ${connection.baseBranch}`, 'forbidden_target');
      }
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
      assertWritableBranch(branch, connection.workingBranch);
      assertWritablePath(path);
      const content = String(input.content);
      if (byteLength(content) > MAX_WRITE_FILE_BYTES) {
        throw new GithubError(`File exceeds the ${MAX_WRITE_FILE_BYTES}-byte write limit`, 'forbidden_target');
      }
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
          content: Buffer.from(content, 'utf-8').toString('base64'),
          branch,
          ...(existingSha ? { sha: existingSha } : {}),
        },
      });
      return { branch, path, commitSha: result.commit?.sha ?? null, updated: Boolean(existingSha) };
    }

    case 'github_commit_files': {
      const branch = String(input.branch);
      assertWritableBranch(branch, connection.workingBranch);
      const files = input.files as Array<{ path: string; content: string }>;
      if (!Array.isArray(files) || files.length === 0 || files.length > MAX_COMMIT_FILES) {
        throw new GithubError(`Commits must contain between 1 and ${MAX_COMMIT_FILES} files`, 'forbidden_target');
      }
      let totalBytes = 0;
      for (const f of files) {
        assertWritablePath(f.path);
        const size = byteLength(f.content);
        if (size > MAX_WRITE_FILE_BYTES) {
          throw new GithubError(`File exceeds the ${MAX_WRITE_FILE_BYTES}-byte write limit: ${f.path}`, 'forbidden_target');
        }
        totalBytes += size;
      }
      if (totalBytes > MAX_COMMIT_BYTES) {
        throw new GithubError(`Commit exceeds the ${MAX_COMMIT_BYTES}-byte total write limit`, 'forbidden_target');
      }
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
      assertWritableBranch(head, connection.workingBranch); // PRs may only come from the configured agent branch
      const base = String(input.base ?? connection.baseBranch);
      if (base !== connection.baseBranch) {
        throw new GithubError(`Pull requests must target the configured base branch: ${connection.baseBranch}`, 'forbidden_target');
      }
      if (base === head) {
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
