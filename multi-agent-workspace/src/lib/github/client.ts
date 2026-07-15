import { createSign } from 'node:crypto';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const API_ROOT = 'https://api.github.com';
const API_VERSION = '2022-11-28';
const USER_AGENT = 'borgimus-multi-agent-workspace';

interface GitHubConfig {
  appId: string;
  installationId: string;
  privateKeyPath: string;
  repository: string;
  owner: string;
  repo: string;
}

interface CachedToken {
  token: string;
  expiresAt: number;
}

const globalCache = globalThis as unknown as { __githubInstallationToken?: CachedToken };

function requiredEnv(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) throw new Error(`GitHub integration is not configured: ${name} is missing`);
  return value;
}

export function getGitHubConfig(): GitHubConfig {
  const repository = requiredEnv('GITHUB_REPOSITORY');
  const parts = repository.split('/');
  if (parts.length !== 2 || parts.some((part) => !/^[A-Za-z0-9_.-]+$/.test(part))) {
    throw new Error('GITHUB_REPOSITORY must use owner/repository format');
  }

  const configuredPath = requiredEnv('GITHUB_APP_PRIVATE_KEY_PATH');
  const privateKeyPath = path.isAbsolute(configuredPath)
    ? configuredPath
    : path.resolve(process.cwd(), configuredPath);

  return {
    appId: requiredEnv('GITHUB_APP_ID'),
    installationId: requiredEnv('GITHUB_INSTALLATION_ID'),
    privateKeyPath,
    repository,
    owner: parts[0]!,
    repo: parts[1]!,
  };
}

function base64Url(value: string): string {
  return Buffer.from(value, 'utf8').toString('base64url');
}

function createAppJwt(config: GitHubConfig): string {
  const now = Math.floor(Date.now() / 1000);
  const unsigned = [
    base64Url(JSON.stringify({ alg: 'RS256', typ: 'JWT' })),
    base64Url(JSON.stringify({ iat: now - 60, exp: now + 540, iss: config.appId })),
  ].join('.');

  let privateKey: string;
  try {
    privateKey = readFileSync(config.privateKeyPath, 'utf8');
  } catch {
    throw new Error('GitHub private key could not be read from GITHUB_APP_PRIVATE_KEY_PATH');
  }

  const signer = createSign('RSA-SHA256');
  signer.update(unsigned);
  signer.end();
  const signature = signer.sign(privateKey).toString('base64url');
  return `${unsigned}.${signature}`;
}

async function parseResponse<T>(response: Response): Promise<T> {
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = null;
    }
  }
  if (!response.ok) {
    const message =
      payload && typeof payload === 'object' && 'message' in payload
        ? String((payload as { message: unknown }).message)
        : `HTTP ${response.status}`;
    throw new Error(`GitHub API request failed: ${message}`);
  }
  return payload as T;
}

async function installationToken(): Promise<string> {
  const cached = globalCache.__githubInstallationToken;
  if (cached && cached.expiresAt - Date.now() > 60_000) return cached.token;

  const config = getGitHubConfig();
  const response = await fetch(
    `${API_ROOT}/app/installations/${encodeURIComponent(config.installationId)}/access_tokens`,
    {
      method: 'POST',
      headers: {
        accept: 'application/vnd.github+json',
        authorization: `Bearer ${createAppJwt(config)}`,
        'x-github-api-version': API_VERSION,
        'user-agent': USER_AGENT,
      },
      cache: 'no-store',
    },
  );

  const payload = await parseResponse<{ token?: string; expires_at?: string }>(response);
  if (!payload.token || !payload.expires_at) {
    throw new Error('GitHub did not return a valid installation token');
  }

  globalCache.__githubInstallationToken = {
    token: payload.token,
    expiresAt: new Date(payload.expires_at).getTime(),
  };
  return payload.token;
}

function allowedApiUrl(apiPath: string, config: GitHubConfig): URL {
  const url = new URL(apiPath, API_ROOT);
  if (url.origin !== API_ROOT) throw new Error('GitHub API path must be relative');

  const repositoryRoot = `/repos/${encodeURIComponent(config.owner)}/${encodeURIComponent(config.repo)}`;
  const isRepositoryRequest =
    url.pathname === repositoryRoot || url.pathname.startsWith(`${repositoryRoot}/`);
  const isScopedCodeSearch =
    url.pathname === '/search/code' &&
    (url.searchParams.get('q') ?? '').includes(`repo:${config.repository}`);

  if (!isRepositoryRequest && !isScopedCodeSearch) {
    throw new Error('GitHub request is outside the configured repository');
  }
  return url;
}

export async function githubRequest<T>(
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE',
  apiPath: string,
  body?: unknown,
): Promise<T> {
  const config = getGitHubConfig();
  const url = allowedApiUrl(apiPath, config);
  const token = await installationToken();
  const response = await fetch(url, {
    method,
    headers: {
      accept: 'application/vnd.github+json',
      authorization: `Bearer ${token}`,
      'content-type': 'application/json',
      'x-github-api-version': API_VERSION,
      'user-agent': USER_AGENT,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: 'no-store',
  });
  return parseResponse<T>(response);
}

export function repositoryApiPath(suffix = ''): string {
  const config = getGitHubConfig();
  return `/repos/${encodeURIComponent(config.owner)}/${encodeURIComponent(config.repo)}${suffix}`;
}

export function assertAllowedRepository(repository: string): void {
  if (repository !== getGitHubConfig().repository) {
    throw new Error('Repository does not match GITHUB_REPOSITORY');
  }
}

export async function verifyGitHubConnection(): Promise<{
  repository: string;
  defaultBranch: string;
  private: boolean;
  permissions: Record<string, string>;
}> {
  const config = getGitHubConfig();
  const repo = await githubRequest<{
    full_name: string;
    default_branch: string;
    private: boolean;
    permissions?: Record<string, boolean>;
  }>('GET', repositoryApiPath());

  assertAllowedRepository(repo.full_name);
  return {
    repository: repo.full_name,
    defaultBranch: repo.default_branch,
    private: repo.private,
    permissions: Object.fromEntries(
      Object.entries(repo.permissions ?? {}).map(([key, value]) => [key, value ? 'allowed' : 'denied']),
    ),
  };
}

export function resetGitHubTokenCacheForTests(): void {
  delete globalCache.__githubInstallationToken;
}
