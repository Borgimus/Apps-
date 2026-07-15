import { execSync } from 'node:child_process';
import { mkdirSync, rmSync } from 'node:fs';
import path from 'node:path';

/**
 * Create a fresh SQLite database for the test run. The DB is a throwaway
 * artifact under tests/tmp/ — we remove the file and push the schema onto a
 * brand-new one (no destructive flags needed).
 */
export default function setup(): void {
  const root = path.resolve(__dirname, '..');
  const dbFile = path.join(root, 'tests/tmp/test.db');
  mkdirSync(path.dirname(dbFile), { recursive: true });
  for (const suffix of ['', '-journal']) rmSync(dbFile + suffix, { force: true });
  execSync('npx prisma db push --skip-generate', {
    cwd: root,
    env: { ...process.env, DATABASE_URL: `file:${dbFile}` },
    stdio: 'pipe',
  });
}
