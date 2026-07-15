import { z } from 'zod';
import { handle, ok, fail, parseBody } from '@/lib/api';
import { prisma } from '@/lib/db';
import { emitActivity } from '@/lib/events';
import {
  assertAllowedRepository,
  githubRequest,
  repositoryApiPath,
  verifyGitHubConnection,
} from '@/lib/github/client';
import { assertWritableBranch } from '@/lib/github/tools';

export const dynamic = 'force-dynamic';

type Params = { params: Promise<{ id: string }> };

export async function GET(_req: Request, { params }: Params) {
  return handle(async () => {
    const { id } = await params;
    const connection = await prisma.repositoryConnection.findUnique({ where: { projectId: id } });
    return ok(connection);
  });
}

const schema = z.object({
  repositoryFullName: z.string().regex(/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/),
  baseBranch: z.string().min(1).max(200),
  workingBranch: z.string().min(1).max(200),
});

export async function PATCH(req: Request, { params }: Params) {
  return handle(async () => {
    const { id } = await params;
    const project = await prisma.project.findUnique({ where: { id } });
    if (!project) return fail('Project not found', 404);

    const parsed = await parseBody(req, schema);
    if ('error' in parsed) return parsed.error;
    const body = parsed.data;

    assertAllowedRepository(body.repositoryFullName);
    assertWritableBranch(body.workingBranch);
    const verified = await verifyGitHubConnection();
    await githubRequest('GET', repositoryApiPath(`/branches/${body.baseBranch.split('/').map(encodeURIComponent).join('/')}`));

    const connection = await prisma.repositoryConnection.upsert({
      where: { projectId: id },
      update: {
        repositoryFullName: body.repositoryFullName,
        baseBranch: body.baseBranch,
        workingBranch: body.workingBranch,
        verifiedAt: new Date(),
      },
      create: {
        projectId: id,
        repositoryFullName: body.repositoryFullName,
        baseBranch: body.baseBranch,
        workingBranch: body.workingBranch,
        verifiedAt: new Date(),
      },
    });

    await emitActivity({
      projectId: id,
      actor: 'user',
      type: 'repository_connected',
      summary: `Connected project to ${connection.repositoryFullName}`,
      data: {
        repository: connection.repositoryFullName,
        baseBranch: connection.baseBranch,
        workingBranch: connection.workingBranch,
        defaultBranch: verified.defaultBranch,
      },
      refId: connection.id,
    });
    return ok(connection);
  });
}
