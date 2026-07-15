import { NextResponse } from 'next/server';
import { ZodTypeAny, z } from 'zod';

export function ok(data: unknown, init?: number): NextResponse {
  return NextResponse.json({ ok: true, data }, { status: init ?? 200 });
}

export function fail(message: string, status = 400): NextResponse {
  return NextResponse.json({ ok: false, error: message }, { status });
}

/** Parse and validate a JSON body against a zod schema. */
export async function parseBody<S extends ZodTypeAny>(
  req: Request,
  schema: S,
): Promise<{ data: z.infer<S> } | { error: NextResponse }> {
  let raw: unknown;
  try {
    raw = await req.json();
  } catch {
    return { error: fail('Invalid JSON body') };
  }
  const parsed = schema.safeParse(raw);
  if (!parsed.success) {
    return { error: fail(`Validation failed: ${parsed.error.issues.map((i) => `${i.path.join('.')}: ${i.message}`).join('; ')}`) };
  }
  return { data: parsed.data };
}

export function handle(fn: () => Promise<NextResponse>): Promise<NextResponse> {
  return fn().catch((err) => {
    console.error('[api]', err);
    return fail(err instanceof Error ? err.message : 'Internal error', 500);
  });
}
