import { AnthropicAdapter } from './anthropic';
import { MockAdapter } from './mock';
import { OpenAICompatAdapter } from './openaiCompat';
import { ProviderAdapter, ProviderError, ProviderRequest, ProviderResponse } from './types';

const adapters: Record<string, ProviderAdapter> = {
  anthropic: new AnthropicAdapter(),
  'openai-compatible': new OpenAICompatAdapter(),
  mock: new MockAdapter(),
};

export function getAdapter(provider: string): ProviderAdapter {
  const adapter = adapters[provider];
  if (!adapter) {
    throw new ProviderError(
      `Unknown provider "${provider}". Available: ${Object.keys(adapters).join(', ')}`,
      'invalid_request',
      false,
    );
  }
  return adapter;
}

export function listProviders(): string[] {
  return Object.keys(adapters);
}

const MAX_ATTEMPTS = 3;
// Connection failures and rate limits deserve a wider retry window than other
// API errors — they clear on their own within seconds to a minute.
const MAX_PATIENT_ATTEMPTS = 5;
const BASE_DELAY_MS = 500;
const MAX_DELAY_MS = 90_000;

function retryDelayMs(err: ProviderError, attempt: number): number {
  // Best signal: the provider told us exactly how long to wait.
  if (err.retryAfterMs != null && err.retryAfterMs > 0) {
    return Math.min(err.retryAfterMs + 500, MAX_DELAY_MS); // +buffer for clock skew
  }
  // Rate limits without a hint: token windows refill per minute, so back off
  // much harder than for transient connection errors.
  if (err.kind === 'rate_limit') {
    return Math.min(2_000 * 3 ** (attempt - 1), 60_000); // 2s, 6s, 18s, 54s
  }
  return BASE_DELAY_MS * 2 ** (attempt - 1);
}

/** Call a provider with retry + exponential backoff on retryable errors. */
export async function callWithRetry(
  provider: string,
  req: ProviderRequest,
  onRetry?: (attempt: number, err: ProviderError) => void | Promise<void>,
): Promise<ProviderResponse> {
  const adapter = getAdapter(provider);
  let lastErr: ProviderError | undefined;
  for (let attempt = 1; attempt <= MAX_PATIENT_ATTEMPTS; attempt++) {
    try {
      return await adapter.call(req);
    } catch (err) {
      const pErr =
        err instanceof ProviderError
          ? err
          : new ProviderError(String(err), 'unknown', false);
      lastErr = pErr;
      const maxAttempts = ['network', 'timeout', 'rate_limit'].includes(pErr.kind)
        ? MAX_PATIENT_ATTEMPTS
        : MAX_ATTEMPTS;
      if (!pErr.retryable || attempt >= maxAttempts) throw pErr;
      await onRetry?.(attempt, pErr);
      await new Promise((r) => setTimeout(r, retryDelayMs(pErr, attempt)));
    }
  }
  throw lastErr ?? new ProviderError('Provider call failed', 'unknown', false);
}

export function computeCostUsd(
  usage: { inputTokens: number; outputTokens: number },
  pricing: { inputPricePerMTok: number; outputPricePerMTok: number },
): number {
  return (
    (usage.inputTokens / 1_000_000) * pricing.inputPricePerMTok +
    (usage.outputTokens / 1_000_000) * pricing.outputPricePerMTok
  );
}
