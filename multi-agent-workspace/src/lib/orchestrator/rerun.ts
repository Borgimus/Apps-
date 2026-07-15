import type { ModelCall } from '@prisma/client';
import { prisma } from '../db';
import { emitActivity } from '../events';
import { parseJson, toJson } from '../json';
import { callWithRetry, computeCostUsd } from '../providers/registry';
import { NormMessage, ProviderError, ToolDef } from '../providers/types';

/**
 * Prompt Inspector rerun: duplicate a historical model call (optionally with an
 * edited system prompt / messages / settings) and execute it as a NEW versioned
 * ModelCall. The original record is never modified, and rerun responses are
 * inspection-only — any tool calls the model requests are recorded but NOT
 * executed.
 */
export async function rerunModelCall(
  modelCallId: string,
  edits: {
    systemPrompt?: string;
    messages?: NormMessage[];
    temperature?: number;
    maxTokens?: number;
    modelConfigId?: string;
  } = {},
): Promise<ModelCall> {
  const original = await prisma.modelCall.findUnique({ where: { id: modelCallId } });
  if (!original) throw new Error('Model call not found');

  let provider = original.provider;
  let modelId = original.modelId;
  let pricing = { inputPricePerMTok: 0, outputPricePerMTok: 0 };
  let baseUrl: string | null = null;
  let apiKeyEnvVar: string | null = null;

  const agent = original.agentId
    ? await prisma.agent.findUnique({ where: { id: original.agentId }, include: { modelConfig: true } })
    : null;
  let mc = agent?.modelConfig ?? null;
  if (edits.modelConfigId) {
    mc = await prisma.modelConfig.findUnique({ where: { id: edits.modelConfigId } });
    if (!mc) throw new Error('Model config not found');
  }
  if (mc) {
    provider = mc.provider;
    modelId = mc.modelId;
    pricing = mc;
    baseUrl = mc.baseUrl;
    apiKeyEnvVar = mc.apiKeyEnvVar;
  }

  const settings = parseJson<{ temperature?: number; maxTokens?: number }>(original.settingsJson, {});
  const system = edits.systemPrompt ?? original.systemPrompt;
  const messages = edits.messages ?? parseJson<NormMessage[]>(original.messagesJson, []);
  const tools = parseJson<ToolDef[]>(original.toolDefsJson, []);
  const temperature = edits.temperature ?? settings.temperature ?? 0.3;
  const maxTokens = edits.maxTokens ?? settings.maxTokens ?? 4096;

  const startedAt = Date.now();
  let responseText = '';
  let toolCallsJson = '[]';
  let stopReason = '';
  let usage = { inputTokens: 0, outputTokens: 0 };
  let status = 'ok';
  let error: string | null = null;

  try {
    const resp = await callWithRetry(provider, {
      modelId,
      system,
      messages,
      tools,
      temperature,
      maxTokens,
      baseUrl,
      apiKey: apiKeyEnvVar ? process.env[apiKeyEnvVar] : undefined,
    });
    responseText = resp.text;
    toolCallsJson = toJson(resp.toolCalls);
    stopReason = resp.stopReason;
    usage = resp.usage;
  } catch (err) {
    status = 'error';
    error = err instanceof ProviderError ? `${err.kind}: ${err.message}` : String(err);
  }

  const costUsd = computeCostUsd(usage, pricing);
  const rerun = await prisma.modelCall.create({
    data: {
      runId: original.runId,
      projectId: original.projectId,
      agentId: original.agentId,
      seq: original.seq,
      provider,
      modelId,
      systemPrompt: system,
      messagesJson: toJson(messages),
      toolDefsJson: original.toolDefsJson,
      settingsJson: toJson({ temperature, maxTokens, rerunOf: original.id }),
      contextJson: original.contextJson,
      responseText,
      toolCallsJson,
      stopReason,
      inputTokens: usage.inputTokens,
      outputTokens: usage.outputTokens,
      costUsd,
      durationMs: Date.now() - startedAt,
      status,
      error,
      parentCallId: original.id,
      version: original.version + 1,
    },
  });
  if (costUsd > 0 || usage.inputTokens > 0) {
    await prisma.usageRecord.create({
      data: {
        projectId: original.projectId,
        agentId: original.agentId,
        runId: original.runId,
        modelCallId: rerun.id,
        provider,
        modelId,
        inputTokens: usage.inputTokens,
        outputTokens: usage.outputTokens,
        costUsd,
      },
    });
  }
  await emitActivity({
    projectId: original.projectId,
    actor: 'user',
    type: 'model_call_rerun',
    summary: `User reran a model call (v${rerun.version}, ${provider}/${modelId})`,
    data: { originalId: original.id, rerunId: rerun.id, edited: Object.keys(edits) },
    refId: rerun.id,
  });
  return rerun;
}
