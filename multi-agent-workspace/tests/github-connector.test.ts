import { generateKeyPairSync } from 'node:crypto';
import { mkdirSync, rmSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  assertAllowedRepository,
  resetGitHubTokenCacheForTests,
  verifyGitHubConnection,
} from '../src/lib/github/client';
import { assertWritableBranch, githubToolPermission } from '../src/lib/github/tools';

const keyPath = path.resolve(__dirname, 'tmp/github-app-test.pem');
const envNames = [
  'GITHUB_APP_ID',
  'GITHUB_INSTALLATION_ID',
  'GITHUB_APP_PRIVATE_KEY_PATH',
  'GITHUB_REPOSITORY',
] as const;
const originalEnv = Object.fromEntries(envNames.map((name) => [name, process.env[name]]));

describe('GitHub App connector', () => {
  beforeEach(() => {
    const { privateKey } = generateKeyPairSync('rsa', { modulusLength: 2048 });
    mkdirSync(path.dirname(keyPath), { recursive: true });
    writeFileSync(keyPath, privateKey.export({ type: 'pkcs8', format: 'pem' }));

    process.env.GITHUB_APP_ID = '4307398';
    process.env.GITHUB_INSTALLATION_ID = '146802834';
    process.env.GITHUB_APP_PRIVATE_KEY_PATH = keyPath;
    process.env.GITHUB_REPOSITORY = 'Borgimus/Apps-';
    resetGitHubTokenCacheForTests();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    resetGitHubTokenCacheForTests();
    rmSync(keyPath, { force: true });
    for (const name of envNames) {
      const value = originalEnv[name];
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
  });

  it('exchanges a signed App JWT for an installation token and verifies the repository', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            token: 'installation-token',
            expires_at: new Date(Date.now() + 3_600_000).toISOString(),
          }),
          { status: 201 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            full_name: 'Borgimus/Apps-',
            default_branch: 'main',
            private: true,
            permissions: { pull: true, push: true },
          }),
          { status: 200 },
        ),
      );
    vi.stubGlobal('fetch', fetchMock);

    await expect(verifyGitHubConnection()).resolves.toEqual({
      repository: 'Borgimus/Apps-',
      defaultBranch: 'main',
      private: true,
      permissions: { pull: 'allowed', push: 'allowed' },
    });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const tokenHeaders = (fetchMock.mock.calls[0]?.[1] as RequestInit).headers as Record<string, string>;
    const repoHeaders = (fetchMock.mock.calls[1]?.[1] as RequestInit).headers as Record<string, string>;
    expect(tokenHeaders.authorization).toMatch(/^Bearer [^.]+\.[^.]+\.[^.]+$/);
    expect(repoHeaders.authorization).toBe('Bearer installation-token');
  });

  it('rejects repositories outside the configured installation scope', () => {
    expect(() => assertAllowedRepository('someone/else')).toThrow(
      'Repository does not match GITHUB_REPOSITORY',
    );
  });

  it('allows only dedicated agent working branches', () => {
    expect(() => assertWritableBranch('agent/github-app-connector')).not.toThrow();
    expect(() => assertWritableBranch('feature/safe-change')).not.toThrow();
    expect(() => assertWritableBranch('main')).toThrow('Writes are restricted');
    expect(() =>
      assertWritableBranch('agent/other', 'agent/github-app-connector'),
    ).toThrow('configured working branch');
  });

  it('maps GitHub tools to explicit agent permissions', () => {
    expect(githubToolPermission('github_read_file')).toBe('read');
    expect(githubToolPermission('github_commit_files')).toBe('write');
    expect(githubToolPermission('github_open_draft_pull_request')).toBe('pull_request');
    expect(githubToolPermission('read_project_file')).toBeNull();
  });
});
