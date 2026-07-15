import {
  NormMessage,
  NormToolCall,
  ProviderAdapter,
  ProviderError,
  ProviderRequest,
  ProviderResponse,
  requestTimeoutMs,
} from './types';

const DEFAULT_BASE_URL = 'https://api.openai.com/v1';

type OaiMessage =
  | { role: 'system' | 'user'; content: string }
  | {
      role: 'assistant';
      content: string | null;
      tool_calls?: Array<{
        id: string;
        type: 'function';
        function: { name: string; arguments: string };
      }>;
    }
  | { role: 'tool'; tool_call_id: string; content: string };

/**
 * Adapter for any OpenAI-compatible chat-completions endpoint:
 * OpenAI, OpenRouter, Ollama (/v1), vLLM, LM Studio, custom gateways.
 */
function toOaiMessages(system: string, messages: NormMessage[]): OaiMessage[] {
  const out: OaiMessage[] = [];
  if (system) out.push({ role: 'system', content: system });
  for (const m of messages) {
    if (m.role === 'user') {
      out.push({ role: 'user', content: m.content });
    } else if (m.role === 'assistant') {
      out.push({
        role: 'assistant',
        content: m.content || null,
        tool_calls:
          m.toolCalls && m.toolCalls.length > 0
            ? m.toolCalls.map((tc) => ({
                id: tc.id,
                type: 'function' as const,
                function: { name: tc.name, arguments: JSON.stringify(tc.input ?? {}) },
              }))
            : undefined,
      });
    } else {
      out.push({ role: 'tool', tool_call_id: m.toolCallId, content: m.content });
    }
  }
  return out;
}

export class OpenAICompatAdapter implements ProviderAdapter {
  readonly id = 'openai-compatible';

  async call(req: ProviderRequest): Promise<ProviderResponse> {
    try {
      return await this.doCall(req, { includeTemperature: true });
    } catch (err) {
      // Some newer models reject the `temperature` parameter. Retry once
      // without it so model configs stay portable across model generations.
      if (
        err instanceof ProviderError &&
        err.kind === 'invalid_request' &&
        /temperature/i.test(err.message)
      ) {
        return this.doCall(req, { includeTemperature: false });
      }
      throw err;
    }
  }

  private async doCall(
    req: ProviderRequest,
    opts: { includeTemperature: boolean },
  ): Promise<ProviderResponse> {
    const baseUrl = (req.baseUrl ?? process.env.OPENAI_BASE_URL ?? DEFAULT_BASE_URL).replace(/\/$/, '');
    const apiKey = req.apiKey ?? process.env.OPENAI_API_KEY ?? 'not-needed'; // local endpoints often ignore the key

    const body = {
      model: req.modelId,
      temperature: opts.includeTemperature ? req.temperature : undefined,
      max_tokens: req.maxTokens,
      messages: toOaiMessages(req.system, req.messages),
      tools:
        req.tools.length > 0
          ? req.tools.map((t) => ({
              type: 'function' as const,
              function: { name: t.name, description: t.description, parameters: t.inputSchema },
            }))
          : undefined,
    };

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), requestTimeoutMs(req.maxTokens));
    let res: Response;
    try {
      res = await fetch(`${baseUrl}/chat/completions`, {
        method: 'POST',
        headers: {
          'content-type': 'application/json',
          authorization: `Bearer ${apiKey}`,
        },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
    } catch (err) {
      const aborted = err instanceof Error && err.name === 'AbortError';
      throw new ProviderError(
        aborted ? 'Request timed out' : `Network error: ${String(err)}`,
        aborted ? 'timeout' : 'network',
        true,
      );
    } finally {
      clearTimeout(timer);
    }

    if (!res.ok) {
      const text = await res.text().catch(() => '');
      if (res.status === 401 || res.status === 403)
        throw new ProviderError(`Auth error: ${text}`, 'auth', false);
      if (res.status === 429) throw new ProviderError(`Rate limit: ${text}`, 'rate_limit', true);
      if (res.status >= 500)
        throw new ProviderError(`Server error (${res.status}): ${text}`, 'overloaded', true);
      throw new ProviderError(`Error ${res.status}: ${text}`, 'invalid_request', false);
    }

    const data = (await res.json()) as {
      choices?: Array<{
        message?: {
          content?: string | null;
          tool_calls?: Array<{ id: string; function?: { name?: string; arguments?: string } }>;
        };
        finish_reason?: string;
      }>;
      usage?: { prompt_tokens?: number; completion_tokens?: number };
    };

    const choice = data.choices?.[0];
    const toolCalls: NormToolCall[] = [];
    for (const tc of choice?.message?.tool_calls ?? []) {
      let input: unknown = {};
      try {
        input = JSON.parse(tc.function?.arguments ?? '{}');
      } catch {
        input = { _raw: tc.function?.arguments };
      }
      toolCalls.push({ id: tc.id, name: tc.function?.name ?? 'unknown', input });
    }

    return {
      text: choice?.message?.content ?? '',
      toolCalls,
      stopReason: choice?.finish_reason ?? 'stop',
      usage: {
        inputTokens: data.usage?.prompt_tokens ?? 0,
        outputTokens: data.usage?.completion_tokens ?? 0,
      },
    };
  }
}
