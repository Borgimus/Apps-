import { z } from 'zod';
import { prisma } from '@/lib/db';
import { handle, ok, fail, parseBody } from '@/lib/api';
import { emitActivity } from '@/lib/events';

export const dynamic = 'force-dynamic';

type Params = { params: Promise<{ id: string }> };

/** File detail with full version history and author attribution. */
export async function GET(_req: Request, { params }: Params) {
  return handle(async () => {
    const { id } = await params;
    const file = await prisma.projectFile.findUnique({
      where: { id },
      include: { versions: { orderBy: { version: 'desc' } } },
    });
    if (!file) return fail('File not found', 404);
    const agents = await prisma.agent.findMany({ select: { id: true, name: true } });
    const names = Object.fromEntries(agents.map((a) => [a.id, a.name]));
    return ok({
      ...file,
      versions: file.versions.map((v) => ({
        ...v,
        author: v.authorAgentId ? names[v.authorAgentId] ?? 'agent' : 'user',
      })),
    });
  });
}

const patchSchema = z.object({
  restoreVersion: z.number().int().positive().optional(),
  content: z.string().optional(), // direct user edit
  note: z.string().default(''),
});

/** User edit or restore — creates a new version, history is never rewritten. */
export async function PATCH(req: Request, { params }: Params) {
  return handle(async () => {
    const { id } = await params;
    const parsed = await parseBody(req, patchSchema);
    if ('error' in parsed) return parsed.error;
    const file = await prisma.projectFile.findUnique({ where: { id }, include: { versions: true } });
    if (!file) return fail('File not found', 404);

    let content: string | undefined = parsed.data.content;
    let note = parsed.data.note;
    if (parsed.data.restoreVersion !== undefined) {
      const v = file.versions.find((x) => x.version === parsed.data.restoreVersion);
      if (!v) return fail('Version not found', 404);
      content = v.content;
      note = note || `Restored from v${v.version}`;
    }
    if (content === undefined) return fail('Nothing to update — pass content or restoreVersion');

    const updated = await prisma.projectFile.update({
      where: { id },
      data: { latestVersion: file.latestVersion + 1, deleted: false },
    });
    await prisma.fileVersion.create({
      data: { fileId: id, version: updated.latestVersion, content, authorAgentId: null, note },
    });
    await emitActivity({
      projectId: file.projectId,
      actor: 'user',
      type: 'file_change',
      summary: `User updated ${file.path} (v${updated.latestVersion})${note ? ` — ${note}` : ''}`,
      data: { path: file.path, version: updated.latestVersion },
      refId: id,
    });
    return ok({ path: file.path, version: updated.latestVersion });
  });
}
