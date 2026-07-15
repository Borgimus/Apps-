import { handle, ok } from '@/lib/api';
import { getGitHubConfig, verifyGitHubConnection } from '@/lib/github/client';

export const dynamic = 'force-dynamic';

export async function GET() {
  return handle(async () => {
    try {
      const config = getGitHubConfig();
      const connection = await verifyGitHubConnection();
      return ok({
        configured: true,
        connected: true,
        repository: connection.repository,
        defaultBranch: connection.defaultBranch,
        private: connection.private,
        permissions: connection.permissions,
        appId: config.appId,
        installationId: config.installationId,
      });
    } catch (error) {
      return ok({
        configured: false,
        connected: false,
        error: error instanceof Error ? error.message : 'GitHub connection failed',
      });
    }
  });
}
