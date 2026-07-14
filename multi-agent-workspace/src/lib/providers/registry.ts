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
const BASE_DELAY_MS = 500;

/** Call a provider with retry + exponential backoff on retryable errors. */
export async function callWithRetry(
  provider: string,
  req: ProviderRequest,
  onRetry?: (attempt: number, err: ProviderError) => void | Promise<void>,
): Promise<ProviderResponse> {
  const adapter = getAdapter(provider);
  let lastErr: ProviderError | undefined;
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    try {
      return await adapter.call(req);
    } catch (err) {
      const pErr =
        err instanceof ProviderError
          ? err
          : new ProviderError(String(err), 'unknown', false);
      lastErr = pErr;
      if (!pErr.retryable || attempt === MAX_ATTEMPTS) throw pErr;
      await onRetry?.(attempt, pErr);
      const delay = BASE_DELAY_MS * 2 ** (attempt - 1);
      await new Promise((r) => setTimeout(r, delay));
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
