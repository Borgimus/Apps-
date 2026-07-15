import {
  NormMessage,
  NormToolCall,
  ProviderAdapter,
  ProviderError,
  ProviderRequest,
  ProviderResponse,
} from './types';

const DEFAULT_BASE_URL = 'https://api.anthropic.com';
const TIMEOUT_MS = 120_000;

type AnthropicContentBlock =
  | { type: 'text'; text: string }
  | { type: 'tool_use'; id: string; name: string; input: unknown }
  | { type: 'tool_result'; tool_use_id: string; content: string };

interface AnthropicMessage {
  role: 'user' | 'assistant';
  content: AnthropicContentBlock[];
}

/** Convert normalized messages to Anthropic's alternating user/assistant blocks. */
function toAnthropicMessages(messages: NormMessage[]): AnthropicMessage[] {
  const out: AnthropicMessage[] = [];
  for (const m of messages) {
    if (m.role === 'user') {
      out.push({ role: 'user', content: [{ type: 'text', text: m.content }] });
    } else if (m.role === 'assistant') {
      const blocks: AnthropicContentBlock[] = [];
      if (m.content) blocks.push({ type: 'text', text: m.content });
      for (const tc of m.toolCalls ?? []) {
        blocks.push({ type: 'tool_use', id: tc.id, name: tc.name, input: tc.input });
      }
      if (blocks.length > 0) out.push({ role: 'assistant', content: blocks });
    } else {
      // tool result → user turn with tool_result block; merge consecutive results
      const block: AnthropicContentBlock = {
        type: 'tool_result',
        tool_use_id: m.toolCallId,
        content: m.content,
      };
      const last = out[out.length - 1];
      if (last && last.role === 'user' && last.content.some((b) => b.type === 'tool_result')) {
        last.content.push(block);
      } else {
        out.push({ role: 'user', content: [block] });
      }
    }
  }
  return out;
}

export class AnthropicAdapter implements ProviderAdapter {
  readonly id = 'anthropic';

  async call(req: ProviderRequest): Promise<ProviderResponse> {
    try {
      return await this.doCall(req, { includeTemperature: true });
    } catch (err) {
      // Newer Anthropic models reject `temperature` ("deprecated for this
      // model"). Retry once without it so model configs stay portable across
      // model generations.
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
    const apiKey = req.apiKey ?? process.env.ANTHROPIC_API_KEY;
    if (!apiKey) {
      throw new ProviderError('ANTHROPIC_API_KEY is not configured', 'auth', false);
    }
    const body = {
      model: req.modelId,
      max_tokens: req.maxTokens,
      temperature: opts.includeTemperature ? req.temperature : undefined,
      system: req.system || undefined,
      messages: toAnthropicMessages(req.messages),
      tools:
        req.tools.length > 0
          ? req.tools.map((t) => ({
              name: t.name,
              description: t.description,
              input_schema: t.inputSchema,
            }))
          : undefined,
    };

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
    let res: Response;
    try {
      res = await fetch(`${req.baseUrl ?? DEFAULT_BASE_URL}/v1/messages`, {
        method: 'POST',
        headers: {
          'content-type': 'application/json',
          'x-api-key': apiKey,
          'anthropic-version': '2023-06-01',
        },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
    } catch (err) {
      const aborted = err instanceof Error && err.name === 'AbortError';
      throw new ProviderError(
        aborted ? 'Anthropic request timed out' : `Network error: ${String(err)}`,
        aborted ? 'timeout' : 'network',
        true,
      );
    } finally {
      clearTimeout(timer);
    }

    if (!res.ok) {
      const text = await res.text().catch(() => '');
      if (res.status === 401 || res.status === 403)
        throw new ProviderError(`Anthropic auth error: ${text}`, 'auth', false);
      if (res.status === 429)
        throw new ProviderError(`Anthropic rate limit: ${text}`, 'rate_limit', true);
      if (res.status === 529 || res.status >= 500)
        throw new ProviderError(`Anthropic overloaded (${res.status}): ${text}`, 'overloaded', true);
      throw new ProviderError(`Anthropic error ${res.status}: ${text}`, 'invalid_request', false);
    }

    const data = (await res.json()) as {
      content?: Array<{ type: string; text?: string; id?: string; name?: string; input?: unknown }>;
      stop_reason?: string;
      usage?: { input_tokens?: number; output_tokens?: number };
    };

    let text = '';
    const toolCalls: NormToolCall[] = [];
    for (const block of data.content ?? []) {
      if (block.type === 'text' && block.text) text += block.text;
      if (block.type === 'tool_use' && block.id && block.name) {
        toolCalls.push({ id: block.id, name: block.name, input: block.input ?? {} });
      }
    }

    return {
      text,
      toolCalls,
      stopReason: data.stop_reason ?? 'end_turn',
      usage: {
        inputTokens: data.usage?.input_tokens ?? 0,
        outputTokens: data.usage?.output_tokens ?? 0,
      },
    };
  }
}
