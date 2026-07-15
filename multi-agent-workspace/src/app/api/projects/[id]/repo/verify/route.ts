import { prisma } from '@/lib/db';
import { handle, ok, fail } from '@/lib/api';
import { emitActivity } from '@/lib/events';
import { GithubError, verifyConnection } from '@/lib/github/auth';

export const dynamic = 'force-dynamic';

/**
 * Test the GitHub connection end to end: env config → App JWT → installation
 * token → repository identity. Fails closed; stores status only (no secrets).
 */
export async function POST(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  return handle(async () => {
    const { id } = await params;
    const project = await prisma.project.findUnique({ where: { id } });
    if (!project) return fail('Project not found', 404);

    try {
      const info = await verifyConnection();
      const connection = await prisma.repoConnection.upsert({
        where: { projectId: id },
        update: { owner: info.owner, repo: info.repo, status: 'connected', lastVerifiedAt: new Date(), lastError: null },
        create: {
          projectId: id,
          owner: info.owner,
          repo: info.repo,
          baseBranch: info.defaultBranch,
          status: 'connected',
          lastVerifiedAt: new Date(),
        },
      });
      await emitActivity({
        projectId: id,
        actor: 'user',
        type: 'status_change',
        summary: `GitHub connection verified: ${info.owner}/${info.repo} (default branch ${info.defaultBranch})`,
        data: { owner: info.owner, repo: info.repo, defaultBranch: info.defaultBranch, private: info.private },
        refId: connection.id,
      });
      return ok({ connection, defaultBranch: info.defaultBranch, private: info.private });
    } catch (err) {
      const message = err instanceof GithubError ? err.message : 'GitHub verification failed';
      await prisma.repoConnection.upsert({
        where: { projectId: id },
        update: { status: 'error', lastError: message },
        create: { projectId: id, owner: '', repo: '', status: 'error', lastError: message },
      });
      await emitActivity({
        projectId: id,
        actor: 'user',
        type: 'error',
        summary: `GitHub connection test failed: ${message.slice(0, 200)}`,
        data: { error: message },
      });
      return fail(message, 502);
    }
  });
}
