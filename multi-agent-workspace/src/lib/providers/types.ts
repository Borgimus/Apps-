/**
 * Provider abstraction. Every model provider is normalized to this interface
 * so agents can be switched between models without touching orchestration.
 */

export type NormToolCall = { id: string; name: string; input: unknown };

export type NormMessage =
  | { role: 'user'; content: string }
  | { role: 'assistant'; content: string; toolCalls?: NormToolCall[] }
  | { role: 'tool'; toolCallId: string; name: string; content: string };

export interface ToolDef {
  name: string;
  description: string;
  /** JSON Schema for the tool input. */
  inputSchema: Record<string, unknown>;
}

export interface ProviderRequest {
  modelId: string;
  system: string;
  messages: NormMessage[];
  tools: ToolDef[];
  temperature: number;
  maxTokens: number;
  baseUrl?: string | null;
  apiKey?: string | null;
  /** Extra deterministic context for the mock provider. */
  meta?: Record<string, unknown>;
}

export interface ProviderUsage {
  inputTokens: number;
  outputTokens: number;
}

export interface ProviderResponse {
  text: string;
  toolCalls: NormToolCall[];
  stopReason: string; // end_turn | tool_use | max_tokens | error
  usage: ProviderUsage;
}

export class ProviderError extends Error {
  constructor(
    message: string,
    public readonly kind:
      | 'auth'
      | 'rate_limit'
      | 'timeout'
      | 'overloaded'
      | 'invalid_request'
      | 'network'
      | 'unknown',
    public readonly retryable: boolean,
  ) {
    super(message);
    this.name = 'ProviderError';
  }
}

export interface ProviderAdapter {
  readonly id: string;
  call(req: ProviderRequest): Promise<ProviderResponse>;
}
