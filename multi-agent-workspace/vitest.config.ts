import path from 'node:path';
import { defineConfig } from 'vitest/config';

const dbPath = path.resolve(__dirname, 'tests/tmp/test.db');

export default defineConfig({
  resolve: {
    alias: { '@': path.resolve(__dirname, 'src') },
  },
  test: {
    environment: 'node',
    fileParallelism: false, // one SQLite test DB, sequential files
    env: { DATABASE_URL: `file:${dbPath}` },
    globalSetup: './tests/global-setup.ts',
    testTimeout: 30_000,
    include: ['tests/**/*.test.ts'],
  },
});
