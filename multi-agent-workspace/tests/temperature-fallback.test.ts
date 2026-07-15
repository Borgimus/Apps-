import { afterEach, describe, expect, it, vi } from 'vitest';
import { AnthropicAdapter } from '@/lib/providers/anthropic';
import { OpenAICompatAdapter } from '@/lib/providers/openaiCompat';
import { ProviderRequest } from '@/lib/providers/types';

/**
 * Regression: newer models reject the `temperature` parameter with a 400
 * ("`temperature` is deprecated for this model"). Adapters must retry once
 * without it instead of failing the agent run.
 */

const req: ProviderRequest = {
  modelId: 'claude-test-model',
  system: 'system',
  messages: [{ role: 'user', content: 'hello' }],
  tools: [],
  temperature: 0.3,
  maxTokens: 100,
  apiKey: 'test-key',
};

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

afterEach(() => vi.unstubAllGlobals());

describe('temperature deprecation fallback', () => {
  it('anthropic: retries without temperature on the deprecation 400', async () => {
    const bodies: Array<Record<string, unknown>> = [];
    vi.stubGlobal('fetch', vi.fn(async (_url: unknown, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      bodies.push(body);
      if ('temperature' in body && body.temperature !== undefined) {
        return jsonResponse(400, {
          type: 'error',
          error: { type: 'invalid_request_error', message: '`temperature` is deprecated for this model.' },
        });
      }
      return jsonResponse(200, {
        content: [{ type: 'text', text: 'ok without temperature' }],
        stop_reason: 'end_turn',
        usage: { input_tokens: 5, output_tokens: 3 },
      });
    }));

    const resp = await new AnthropicAdapter().call(req);
    expect(resp.text).toBe('ok without temperature');
    expect(bodies).toHaveLength(2);
    expect(bodies[0]).toHaveProperty('temperature', 0.3);
    expect(bodies[1]).not.toHaveProperty('temperature');
  });

  it('anthropic: does not retry unrelated 400s', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      jsonResponse(400, { type: 'error', error: { type: 'invalid_request_error', message: 'max_tokens too large' } }),
    ));
    await expect(new AnthropicAdapter().call(req)).rejects.toMatchObject({ kind: 'invalid_request' });
    expect(vi.mocked(fetch).mock.calls).toHaveLength(1);
  });

  it('openai-compatible: retries without temperature on a temperature 400', async () => {
    const bodies: Array<Record<string, unknown>> = [];
    vi.stubGlobal('fetch', vi.fn(async (_url: unknown, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      bodies.push(body);
      if ('temperature' in body && body.temperature !== undefined) {
        return jsonResponse(400, {
          error: { message: "Unsupported parameter: 'temperature' is not supported with this model." },
        });
      }
      return jsonResponse(200, {
        choices: [{ message: { content: 'ok' }, finish_reason: 'stop' }],
        usage: { prompt_tokens: 5, completion_tokens: 2 },
      });
    }));

    const resp = await new OpenAICompatAdapter().call({ ...req, modelId: 'gpt-test', baseUrl: 'https://example.test/v1' });
    expect(resp.text).toBe('ok');
    expect(bodies).toHaveLength(2);
    expect(bodies[1]).not.toHaveProperty('temperature');
  });
});
