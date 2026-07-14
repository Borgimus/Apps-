import { z } from 'zod';
import { handle, ok, parseBody } from '@/lib/api';
import { rerunModelCall } from '@/lib/orchestrator/rerun';

export const dynamic = 'force-dynamic';

const messageSchema = z.union([
  z.object({ role: z.literal('user'), content: z.string() }),
  z.object({
    role: z.literal('assistant'),
    content: z.string(),
    toolCalls: z
      .array(
        z
          .object({ id: z.string(), name: z.string(), input: z.unknown() })
          .transform((t) => ({ id: t.id, name: t.name, input: t.input ?? {} })),
      )
      .optional(),
  }),
  z.object({ role: z.literal('tool'), toolCallId: z.string(), name: z.string(), content: z.string() }),
]);

const schema = z.object({
  systemPrompt: z.string().optional(),
  messages: z.array(messageSchema).optional(),
  temperature: z.number().min(0).max(2).optional(),
  maxTokens: z.number().int().positive().optional(),
  modelConfigId: z.string().optional(),
});

/**
 * Duplicate/edit/rerun a prompt. Creates a NEW versioned ModelCall — the
 * original is never overwritten. Tool calls in the response are not executed.
 */
export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  return handle(async () => {
    const { id } = await params;
    const parsed = await parseBody(req, schema);
    if ('error' in parsed) return parsed.error;
    const rerun = await rerunModelCall(id, parsed.data);
    return ok(rerun, 201);
  });
}
