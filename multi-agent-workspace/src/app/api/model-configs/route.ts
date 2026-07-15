import { z } from 'zod';
import { prisma } from '@/lib/db';
import { handle, ok, parseBody } from '@/lib/api';
import { listProviders } from '@/lib/providers/registry';

export const dynamic = 'force-dynamic';

export async function GET() {
  return handle(async () => {
    const configs = await prisma.modelConfig.findMany({ orderBy: { name: 'asc' } });
    // API keys never live in the DB — only env var *names* are stored.
    return ok({ configs, providers: listProviders() });
  });
}

const createSchema = z.object({
  name: z.string().min(1).max(100),
  provider: z.enum(['anthropic', 'openai-compatible', 'mock']),
  modelId: z.string().min(1),
  baseUrl: z.string().url().nullable().optional(),
  apiKeyEnvVar: z.string().regex(/^[A-Z][A-Z0-9_]*$/, 'Must be an env var name').nullable().optional(),
  temperature: z.number().min(0).max(2).default(0.3),
  maxTokens: z.number().int().positive().max(200000).default(4096),
  contextWindow: z.number().int().positive().default(128000),
  inputPricePerMTok: z.number().min(0).default(0),
  outputPricePerMTok: z.number().min(0).default(0),
});

export async function POST(req: Request) {
  return handle(async () => {
    const parsed = await parseBody(req, createSchema);
    if ('error' in parsed) return parsed.error;
    const config = await prisma.modelConfig.create({
      data: { ...parsed.data, baseUrl: parsed.data.baseUrl ?? null, apiKeyEnvVar: parsed.data.apiKeyEnvVar ?? null },
    });
    return ok(config, 201);
  });
}
