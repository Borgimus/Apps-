import { describe, expect, it } from 'vitest';
import { MockAdapter } from '@/lib/providers/mock';
import { AnthropicAdapter } from '@/lib/providers/anthropic';
import { callWithRetry, computeCostUsd, getAdapter } from '@/lib/providers/registry';
import { ProviderError, ProviderRequest, requestTimeoutMs } from '@/lib/providers/types';
import { toolDefsFor } from '@/lib/tools/defs';

function req(objective: string, system = 'ROLE: Generalist'): ProviderRequest {
  return {
    modelId: 'mock-1',
    system,
    messages: [{ role: 'user', content: objective }],
    tools: toolDefsFor(['write_file', 'read_file', 'complete_task', 'create_task', 'send_message', 'record_decision']),
    temperature: 0,
    maxTokens: 1024,
  };
}

describe('mock provider', () => {
  it('is deterministic for the same request', async () => {
    const mock = new MockAdapter();
    const a = await mock.call(req('[test:noop] do nothing'));
    const b = await mock.call(req('[test:noop] do nothing'));
    expect(a.usage).toEqual(b.usage);
    expect(a.toolCalls.map((t) => t.name)).toEqual(b.toolCalls.map((t) => t.name));
    expect(a.toolCalls[0]?.name).toBe('complete_task');
  });

  it('follows the project-manager decomposition script', async () => {
    const mock = new MockAdapter();
    const res = await mock.call(req('Decompose the feature request', 'ROLE: Project Manager'));
    expect(res.toolCalls.map((t) => t.name)).toEqual(['create_task', 'create_task', 'create_task']);
  });

  it('reviewer requests changes on unguarded divide, approves guarded code', async () => {
    const mock = new MockAdapter();
    const base = req('Review the implementation', 'ROLE: Code Reviewer');
    const round1 = await mock.call({
      ...base,
      messages: [
        { role: 'user', content: 'Review the implementation' },
        { role: 'assistant', content: 'Reviewing.', toolCalls: [{ id: 't1', name: 'read_file', input: { path: 'src/calculator.js' } }] },
        { role: 'tool', toolCallId: 't1', name: 'read_file', content: 'function divide(a, b) { return a / b; }' },
      ],
    });
    expect(JSON.stringify(round1.toolCalls)).toContain('CHANGES REQUESTED');
    const round2 = await mock.call({
      ...base,
      messages: [
        { role: 'user', content: 'Review the implementation' },
        { role: 'assistant', content: 'Reviewing.', toolCalls: [{ id: 't1', name: 'read_file', input: { path: 'src/calculator.js' } }] },
        { role: 'tool', toolCallId: 't1', name: 'read_file', content: "if (b === 0) throw new RangeError('Division by zero');" },
      ],
    });
    expect(JSON.stringify(round2.toolCalls)).toContain('APPROVED');
  });

  it('throws a retryable provider error on [test:error]', async () => {
    const mock = new MockAdapter();
    await expect(mock.call(req('[test:error] fail please'))).rejects.toMatchObject({ retryable: true });
  });
});

describe('provider registry', () => {
  it('rejects unknown providers', () => {
    expect(() => getAdapter('nope')).toThrow(ProviderError);
  });

  it('retries retryable errors with backoff and then gives up', async () => {
    const attempts: number[] = [];
    await expect(
      callWithRetry('mock', req('[test:error] fail'), (attempt) => {
        attempts.push(attempt);
      }),
    ).rejects.toBeInstanceOf(ProviderError);
    expect(attempts).toEqual([1, 2]); // two retries before the third, final attempt
  }, 15_000);

  it('gives connection-level failures a wider retry window than API errors', async () => {
    const attempts: number[] = [];
    await expect(
      callWithRetry('mock', req('[test:network-error] wifi blip'), (attempt) => {
        attempts.push(attempt);
      }),
    ).rejects.toMatchObject({ kind: 'network' });
    expect(attempts).toEqual([1, 2, 3, 4]); // four retries before the fifth, final attempt
  }, 20_000);

  it('scales the request timeout with the requested output size', () => {
    expect(requestTimeoutMs(4096)).toBeCloseTo(60_000 + 4096 * 25); // ~2.7 min
    expect(requestTimeoutMs(16384)).toBeCloseTo(60_000 + 16384 * 25); // ~7.8 min — a 16k-token synthesis fits
    expect(requestTimeoutMs(1_000_000)).toBe(600_000); // capped at 10 min
  });

  it('computes cost from per-MTok pricing', () => {
    const cost = computeCostUsd(
      { inputTokens: 1_000_000, outputTokens: 500_000 },
      { inputPricePerMTok: 3, outputPricePerMTok: 15 },
    );
    expect(cost).toBeCloseTo(3 + 7.5);
  });
});

describe('anthropic adapter', () => {
  it('fails fast with a non-retryable auth error when no key is configured', async () => {
    const saved = process.env.ANTHROPIC_API_KEY;
    delete process.env.ANTHROPIC_API_KEY;
    try {
      const adapter = new AnthropicAdapter();
      await expect(adapter.call({ ...req('hello'), modelId: 'claude-sonnet-5' })).rejects.toMatchObject({
        kind: 'auth',
        retryable: false,
      });
    } finally {
      if (saved) process.env.ANTHROPIC_API_KEY = saved;
    }
  });
});
