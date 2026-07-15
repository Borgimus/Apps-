import { createPrivateKey, createSign } from 'node:crypto';
import { readFileSync } from 'node:fs';

/**
 * GitHub App authentication — SERVER ONLY.
 *
 * Security contract:
 *  - The private key is read from GITHUB_APP_PRIVATE_KEY_PATH at call time,
 *    used to sign a short-lived JWT, and never leaves this module. It is
 *    never logged, never included in errors, never persisted, and never
 *    returned to the browser or to a model.
 *  - Installation tokens are short-lived (~1h), cached in process memory
 *    only, and requested with access restricted to the single repository
 *    named in GITHUB_REPOSITORY.
 *  - Everything fails closed: missing/invalid configuration, failed
 *    authentication, or a repository identity mismatch raises GithubError
 *    before any API call is attempted.
 */

const GITHUB_API = 'https://api.github.com';

export class GithubError extends Error {
  constructor(
    message: string,
    public readonly code:
      | 'not_configured'
      | 'auth_failed'
      | 'repo_mismatch'
      | 'forbidden_target'
      | 'api_error'
      | 'not_found',
  ) {
    // Never allow key material or tokens into error text.
    super(redactSecretsFromText(message));
    this.name = 'GithubError';
  }
}

const SECRET_RE = /(-----BEGIN[\s\S]*?-----END[^-]*-----|ghs_[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{16,}|Bearer\s+[A-Za-z0-9._-]{8,})/g;
export function redactSecretsFromText(text: string): string {
  return text.replace(SECRET_RE, '[REDACTED]');
}

export interface AllowedRepo {
  owner: string;
  repo: string;
}

/** The single repository this workspace may touch. Fails closed. */
export function getAllowedRepo(): AllowedRepo {
  const full = process.env.GITHUB_REPOSITORY?.trim();
  if (!full) throw new GithubError('GITHUB_REPOSITORY is not configured', 'not_configured');
  const m = /^([A-Za-z0-9_.-]+)\/([A-Za-z0-9_.-]+)$/.exec(full);
  if (!m) throw new GithubError('GITHUB_REPOSITORY must be "owner/repo"', 'not_configured');
  return { owner: m[1]!, repo: m[2]! };
}

function requiredEnv(name: string): string {
  const v = process.env[name]?.trim();
  if (!v) throw new GithubError(`${name} is not configured`, 'not_configured');
  return v;
}

function b64url(input: Buffer | string): string {
  return Buffer.from(input).toString('base64url');
}

/** RS256 App JWT with a 9-minute future expiry and 60-second backdated issue time. */
function buildAppJwt(): string {
  const appId = requiredEnv('GITHUB_APP_ID');
  const keyPath = requiredEnv('GITHUB_APP_PRIVATE_KEY_PATH');
  let key: ReturnType<typeof createPrivateKey>;
  try {
    key = createPrivateKey(readFileSync(keyPath));
  } catch {
    // Deliberately generic: never echo path contents or key material.
    throw new GithubError('Unable to load the GitHub App private key from GITHUB_APP_PRIVATE_KEY_PATH', 'not_configured');
  }
  const now = Math.floor(Date.now() / 1000);
  const header = b64url(JSON.stringify({ alg: 'RS256', typ: 'JWT' }));
  // GitHub rejects exp values beyond its 10-minute ceiling. Keep one minute
  // of headroom for host/GitHub clock skew and request transit time.
  const payload = b64url(JSON.stringify({ iat: now - 60, exp: now + 540, iss: appId }));
  const signer = createSign('RSA-SHA256');
  signer.update(`${header}.${payload}`);
  const signature = signer.sign(key).toString('base64url');
  return `${header}.${payload}.${signature}`;
}

interface CachedToken {
  token: string;
  expiresAtMs: number;
}

const g = globalThis as unknown as { __ghToken?: CachedToken };

/**
 * Mint (or reuse) a short-lived installation token restricted to the allowed
 * repository. In-memory cache only; refreshed 5 minutes before expiry.
 */
export async function getInstallationToken(): Promise<string> {
  const cached = g.__ghToken;
  if (cached && Date.now() < cached.expiresAtMs - 5 * 60_000) return cached.token;

  const installationId = requiredEnv('GITHUB_INSTALLATION_ID');
  const { repo } = getAllowedRepo();
  const jwt = buildAppJwt();

  let res: Response;
  try {
    res = await fetch(`${GITHUB_API}/app/installations/${installationId}/access_tokens`, {
      method: 'POST',
      headers: {
        authorization: `Bearer ${jwt}`,
        accept: 'application/vnd.github+json',
        'x-github-api-version': '2022-11-28',
      },
      body: JSON.stringify({ repositories: [repo] }), // token scoped to the one repo
    });
  } catch (err) {
    throw new GithubError(`GitHub token request failed: ${String(err)}`, 'auth_failed');
  }
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new GithubError(`GitHub App authentication failed (${res.status}): ${text.slice(0, 300)}`, 'auth_failed');
  }
  const data = (await res.json()) as { token?: string; expires_at?: string };
  if (!data.token) throw new GithubError('GitHub returned no installation token', 'auth_failed');
  g.__ghToken = {
    token: data.token,
    expiresAtMs: data.expires_at ? Date.parse(data.expires_at) : Date.now() + 55 * 60_000,
  };
  return data.token;
}

/** For tests and diagnostics only — clears the in-memory token cache. */
export function clearTokenCache(): void {
  g.__ghToken = undefined;
}

/**
 * Repo-scoped GitHub API request. Only paths inside the allowed repository
 * (or code search constrained to it) can ever be built, so the token cannot
 * be used against any other repository even if a tool is buggy.
 */
export async function ghRequest<T>(
  path: string,
  opts: { method?: string; body?: unknown; accept?: string; raw?: boolean } = {},
): Promise<T> {
  const { owner, repo } = getAllowedRepo();
  const repoPrefix = `/repos/${owner}/${repo}/`;
  const repoRoot = `/repos/${owner}/${repo}`;
  const isSearch = path.startsWith('/search/code?') && path.includes(encodeURIComponent(`repo:${owner}/${repo}`));
  if (!(path === repoRoot || path.startsWith(repoPrefix) || isSearch)) {
    throw new GithubError(`Refusing request outside the allowed repository: ${path.split('?')[0]}`, 'repo_mismatch');
  }

  const token = await getInstallationToken();
  let res: Response;
  try {
    res = await fetch(`${GITHUB_API}${path}`, {
      method: opts.method ?? 'GET',
      headers: {
        authorization: `Bearer ${token}`,
        accept: opts.accept ?? 'application/vnd.github+json',
        'x-github-api-version': '2022-11-28',
        ...(opts.body !== undefined ? { 'content-type': 'application/json' } : {}),
      },
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
    });
  } catch (err) {
    throw new GithubError(`GitHub request failed: ${String(err)}`, 'api_error');
  }
  if (res.status === 404) throw new GithubError(`Not found: ${path.split('?')[0]}`, 'not_found');
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new GithubError(`GitHub API error ${res.status} on ${path.split('?')[0]}: ${text.slice(0, 300)}`, 'api_error');
  }
  if (opts.raw) return (await res.text()) as unknown as T;
  return (await res.json()) as T;
}

/**
 * Verify configuration + authentication + repository identity end to end.
 * Returns repo facts for the UI; never returns credentials.
 */
export async function verifyConnection(): Promise<{
  owner: string;
  repo: string;
  defaultBranch: string;
  private: boolean;
}> {
  const { owner, repo } = getAllowedRepo();
  const data = await ghRequest<{ full_name?: string; default_branch?: string; private?: boolean }>(
    `/repos/${owner}/${repo}`,
  );
  if (data.full_name?.toLowerCase() !== `${owner}/${repo}`.toLowerCase()) {
    throw new GithubError(
      `Repository identity mismatch: expected ${owner}/${repo}, got ${data.full_name ?? 'unknown'}`,
      'repo_mismatch',
    );
  }
  return {
    owner,
    repo,
    defaultBranch: data.default_branch ?? 'main',
    private: data.private ?? true,
  };
}
