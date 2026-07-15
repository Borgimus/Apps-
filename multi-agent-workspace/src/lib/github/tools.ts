import { prisma } from '../db';
import {
  assertAllowedRepository,
  githubRequest,
  repositoryApiPath,
} from './client';

export const GITHUB_READ_TOOLS = [
  'github_list_tree',
  'github_read_file',
  'github_search_code',
  'github_read_branch',
  'github_read_pull_request',
  'github_read_diff',
  'github_read_checks',
] as const;

export const GITHUB_WRITE_TOOLS = [
  'github_create_branch',
  'github_write_file',
  'github_commit_files',
] as const;

export const GITHUB_PR_TOOLS = ['github_open_draft_pull_request'] as const;

const ALL_GITHUB_TOOLS = new Set<string>([
  ...GITHUB_READ_TOOLS,
  ...GITHUB_WRITE_TOOLS,
  ...GITHUB_PR_TOOLS,
]);

export type GitHubPermission = 'read' | 'write' | 'pull_request';

export function isGitHubTool(name: string): boolean {
  return ALL_GITHUB_TOOLS.has(name);
}

export function githubToolPermission(name: string): GitHubPermission | null {
  if ((GITHUB_READ_TOOLS as readonly string[]).includes(name)) return 'read';
  if ((GITHUB_WRITE_TOOLS as readonly string[]).includes(name)) return 'write';
  if ((GITHUB_PR_TOOLS as readonly string[]).includes(name)) return 'pull_request';
  return null;
}

function encodePath(value: string): string {
  return value.split('/').map(encodeURIComponent).join('/');
}

function encodeRef(value: string): string {
  return encodeURIComponent(value);
}

function cleanPath(value: unknown): string {
  const result = String(value ?? '').replaceAll('\\', '/').replace(/^\/+/, '');
  if (!result || result.split('/').some((segment) => segment === '..' || segment === '.')) {
    throw new Error('Invalid repository path');
  }
  return result;
}

function cleanRef(value: unknown): string {
  const result = String(value ?? '').trim();
  if (!result || result.startsWith('/') || result.endsWith('/') || result.includes('..')) {
    throw new Error('Invalid Git ref');
  }
  return result;
}

export function assertWritableBranch(branch: string, configuredWorkingBranch?: string | null): void {
  const validPrefix = ['agent/', 'agents/', 'feature/'].some((prefix) => branch.startsWith(prefix));
  if (!validPrefix) {
    throw new Error('Writes are restricted to agent/, agents/, or feature/ branches');
  }
  if (configuredWorkingBranch && branch !== configuredWorkingBranch) {
    throw new Error(`Writes are restricted to the configured working branch: ${configuredWorkingBranch}`);
  }
}

async function connectionFor(projectId: string) {
  const connection = await prisma.repositoryConnection.findUnique({ where: { projectId } });
  if (!connection) throw new Error('This project is not connected to a GitHub repository');
  assertAllowedRepository(connection.repositoryFullName);
  return connection;
}

type ToolResult = { ok: boolean; output: unknown; error?: string };

export async function executeGitHubTool(
  projectId: string,
  toolName: string,
  input: Record<string, unknown>,
): Promise<ToolResult> {
  try {
    const connection = await connectionFor(projectId);

    switch (toolName) {
      case 'github_list_tree': {
        const ref = cleanRef(input.ref ?? connection.baseBranch);
        const result = await githubRequest<{
          sha: string;
          truncated: boolean;
          tree: Array<{ path: string; type: string; size?: number; sha: string }>;
        }>('GET', repositoryApiPath(`/git/trees/${encodeRef(ref)}?recursive=1`));
        return {
          ok: true,
          output: {
            ref,
            sha: result.sha,
            truncated: result.truncated,
            files: result.tree
              .filter((entry) => entry.type === 'blob')
              .slice(0, 2000)
              .map((entry) => ({ path: entry.path, size: entry.size, sha: entry.sha })),
          },
        };
      }

      case 'github_read_file': {
        const path = cleanPath(input.path);
        const ref = cleanRef(input.ref ?? connection.baseBranch);
        const result = await githubRequest<{
          type: string;
          content?: string;
          encoding?: string;
          sha: string;
          size: number;
          html_url?: string;
        }>('GET', repositoryApiPath(`/contents/${encodePath(path)}?ref=${encodeURIComponent(ref)}`));
        if (result.type !== 'file' || !result.content || result.encoding !== 'base64') {
          throw new Error('Requested path is not a readable file');
        }
        if (result.size > 300_000) throw new Error('File exceeds the 300 KB agent read limit');
        return {
          ok: true,
          output: {
            path,
            ref,
            sha: result.sha,
            size: result.size,
            content: Buffer.from(result.content.replace(/\n/g, ''), 'base64').toString('utf8'),
            url: result.html_url,
          },
        };
      }

      case 'github_search_code': {
        const query = String(input.query ?? '').trim();
        if (!query) throw new Error('Search query is required');
        const q = encodeURIComponent(`${query} repo:${connection.repositoryFullName}`);
        const result = await githubRequest<{
          total_count: number;
          items: Array<{ name: string; path: string; sha: string; html_url: string }>;
        }>('GET', `/search/code?q=${q}&per_page=50`);
        return { ok: true, output: { total: result.total_count, items: result.items } };
      }

      case 'github_read_branch': {
        const branch = cleanRef(input.branch ?? connection.baseBranch);
        const result = await githubRequest<{
          name: string;
          protected: boolean;
          commit: { sha: string; html_url?: string };
        }>('GET', repositoryApiPath(`/branches/${encodeRef(branch)}`));
        return { ok: true, output: result };
      }

      case 'github_read_pull_request': {
        const number = Number(input.number);
        const result = await githubRequest<unknown>('GET', repositoryApiPath(`/pulls/${number}`));
        return { ok: true, output: result };
      }

      case 'github_read_diff': {
        const number = Number(input.number);
        const result = await githubRequest<Array<{
          filename: string;
          status: string;
          additions: number;
          deletions: number;
          changes: number;
          patch?: string;
        }>>('GET', repositoryApiPath(`/pulls/${number}/files?per_page=100`));
        return {
          ok: true,
          output: result.map((file) => ({
            ...file,
            patch: file.patch?.slice(0, 12_000),
          })),
        };
      }

      case 'github_read_checks': {
        const ref = cleanRef(input.ref ?? connection.baseBranch);
        const [checks, status] = await Promise.all([
          githubRequest<unknown>('GET', repositoryApiPath(`/commits/${encodeRef(ref)}/check-runs?per_page=100`)),
          githubRequest<unknown>('GET', repositoryApiPath(`/commits/${encodeRef(ref)}/status`)),
        ]);
        return { ok: true, output: { ref, checks, status } };
      }

      case 'github_create_branch': {
        const branch = cleanRef(input.branch);
        assertWritableBranch(branch, connection.workingBranch);
        const baseRef = cleanRef(input.baseRef ?? connection.baseBranch);
        const base = await githubRequest<{ object: { sha: string } }>(
          'GET',
          repositoryApiPath(`/git/ref/heads/${encodeRef(baseRef)}`),
        );
        const created = await githubRequest<unknown>('POST', repositoryApiPath('/git/refs'), {
          ref: `refs/heads/${branch}`,
          sha: base.object.sha,
        });
        return { ok: true, output: created };
      }

      case 'github_write_file': {
        const branch = cleanRef(input.branch ?? connection.workingBranch);
        assertWritableBranch(branch, connection.workingBranch);
        const path = cleanPath(input.path);
        const content = String(input.content ?? '');
        if (Buffer.byteLength(content, 'utf8') > 500_000) {
          throw new Error('File exceeds the 500 KB agent write limit');
        }
        const payload: Record<string, unknown> = {
          branch,
          message: String(input.message ?? `Update ${path}`).slice(0, 200),
          content: Buffer.from(content, 'utf8').toString('base64'),
        };
        if (typeof input.sha === 'string' && input.sha) payload.sha = input.sha;
        const result = await githubRequest<unknown>(
          'PUT',
          repositoryApiPath(`/contents/${encodePath(path)}`),
          payload,
        );
        return { ok: true, output: result };
      }

      case 'github_commit_files': {
        const branch = cleanRef(input.branch ?? connection.workingBranch);
        assertWritableBranch(branch, connection.workingBranch);
        const files = input.files as Array<{ path: string; content: string }>;
        if (!Array.isArray(files) || files.length === 0 || files.length > 20) {
          throw new Error('Commit must contain between 1 and 20 files');
        }

        const ref = await githubRequest<{ object: { sha: string } }>(
          'GET',
          repositoryApiPath(`/git/ref/heads/${encodeRef(branch)}`),
        );
        const parent = await githubRequest<{ tree: { sha: string } }>(
          'GET',
          repositoryApiPath(`/git/commits/${ref.object.sha}`),
        );

        const tree = [];
        for (const file of files) {
          const filePath = cleanPath(file.path);
          if (Buffer.byteLength(file.content, 'utf8') > 500_000) {
            throw new Error(`File exceeds the 500 KB limit: ${filePath}`);
          }
          const blob = await githubRequest<{ sha: string }>('POST', repositoryApiPath('/git/blobs'), {
            content: file.content,
            encoding: 'utf-8',
          });
          tree.push({ path: filePath, mode: '100644', type: 'blob', sha: blob.sha });
        }

        const newTree = await githubRequest<{ sha: string }>('POST', repositoryApiPath('/git/trees'), {
          base_tree: parent.tree.sha,
          tree,
        });
        const commit = await githubRequest<{ sha: string; html_url?: string }>(
          'POST',
          repositoryApiPath('/git/commits'),
          {
            message: String(input.message ?? 'Agent changes').slice(0, 200),
            tree: newTree.sha,
            parents: [ref.object.sha],
          },
        );
        await githubRequest<unknown>(
          'PATCH',
          repositoryApiPath(`/git/refs/heads/${encodeRef(branch)}`),
          { sha: commit.sha, force: false },
        );
        return { ok: true, output: commit };
      }

      case 'github_open_draft_pull_request': {
        const branch = cleanRef(input.branch ?? connection.workingBranch);
        assertWritableBranch(branch, connection.workingBranch);
        const result = await githubRequest<unknown>('POST', repositoryApiPath('/pulls'), {
          title: String(input.title).slice(0, 200),
          body: String(input.body ?? ''),
          head: branch,
          base: connection.baseBranch,
          draft: true,
        });
        return { ok: true, output: result };
      }

      default:
        return { ok: false, output: null, error: `Unknown GitHub tool: ${toolName}` };
    }
  } catch (error) {
    return { ok: false, output: null, error: error instanceof Error ? error.message : 'GitHub tool failed' };
  }
}
