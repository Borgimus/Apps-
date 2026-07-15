import { z } from 'zod';
import { prisma } from '@/lib/db';
import { handle, ok, fail, parseBody } from '@/lib/api';
import { getAllowedRepo, GithubError } from '@/lib/github/auth';
import { assertWritableBranch, PROTECTED_BRANCHES, WRITABLE_PREFIXES } from '@/lib/github/tools';

export const dynamic = 'force-dynamic';

type Params = { params: Promise<{ id: string }> };

/** Current repository configuration + whether env config exists. No secrets. */
export async function GET(_req: Request, { params }: Params) {
  return handle(async () => {
    const { id } = await params;
    const connection = await prisma.repoConnection.findUnique({ where: { projectId: id } });
    let envRepo: { owner: string; repo: string } | null = null;
    let envError: string | null = null;
    try {
      envRepo = getAllowedRepo();
    } catch (err) {
      envError = err instanceof GithubError ? err.message : 'GitHub is not configured';
    }
    const envConfigured =
      Boolean(process.env.GITHUB_APP_ID) &&
      Boolean(process.env.GITHUB_INSTALLATION_ID) &&
      Boolean(process.env.GITHUB_APP_PRIVATE_KEY_PATH) &&
      envRepo !== null;
    return ok({
      connection,
      envRepo,
      envConfigured,
      envError,
      writablePrefixes: WRITABLE_PREFIXES,
      protectedBranches: [...PROTECTED_BRANCHES],
    });
  });
}

const putSchema = z.object({
  baseBranch: z.string().min(1).max(200).optional(),
  workingBranch: z
    .string()
    .max(200)
    .refine(
      (b) => b === '' || WRITABLE_PREFIXES.some((p) => b.startsWith(p)),
      `Working branch must start with ${WRITABLE_PREFIXES.join(', ')}`,
    )
    .optional(),
});

export async function PUT(req: Request, { params }: Params) {
  return handle(async () => {
    const { id } = await params;
    const project = await prisma.project.findUnique({ where: { id }, select: { id: true } });
    if (!project) return fail('Project not found', 404);
    const parsed = await parseBody(req, putSchema);
    if ('error' in parsed) return parsed.error;
    let envRepo;
    try {
      envRepo = getAllowedRepo();
      if (parsed.data.workingBranch) assertWritableBranch(parsed.data.workingBranch);
    } catch (err) {
      return fail(err instanceof GithubError ? err.message : 'GitHub is not configured', 400);
    }
    const connection = await prisma.repoConnection.upsert({
      where: { projectId: id },
      update: { ...parsed.data, owner: envRepo.owner, repo: envRepo.repo },
      create: {
        projectId: id,
        owner: envRepo.owner,
        repo: envRepo.repo,
        baseBranch: parsed.data.baseBranch ?? 'main',
        workingBranch: parsed.data.workingBranch ?? '',
      },
    });
    return ok(connection);
  });
}
