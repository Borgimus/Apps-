import type { AgentRun } from '@prisma/client';
import { prisma } from '../db';
import { emitActivity, notify } from '../events';
import { parseJson, toJson } from '../json';
import { callWithRetry, computeCostUsd } from '../providers/registry';
import { NormMessage, NormToolCall, ProviderError } from '../providers/types';
import { toolDefsFor, TOOL_SPECS } from '../tools/defs';
import { AgentPermissions, executeTool, toolNeedsApproval, ToolContext } from '../tools/execute';
import { assembleContext } from './prompt';

/**
 * Orchestration engine.
 *
 * A run is durable: its transcript and status live in the database, the loop
 * re-reads status every iteration (so pause/cancel take effect between steps),
 * and a run interrupted by a process restart can be resumed from its
 * transcript. Only one in-process worker may drive a run at a time.
 */

const g = globalThis as unknown as { __activeRuns?: Set<string> };
const activeRuns: Set<string> = g.__activeRuns ?? new Set();
g.__activeRuns = activeRuns;

export interface StartRunOptions {
  agentId: string;
  projectId: string;
  taskId?: string | null;
  objective: string;
  maxIterations?: number;
  idempotencyKey?: string;
}

/** Create a run (idempotent when a key is supplied). Does not process it. */
export async function createRun(opts: StartRunOptions): Promise<AgentRun> {
  if (opts.idempotencyKey) {
    const existing = await prisma.agentRun.findUnique({
      where: { idempotencyKey: opts.idempotencyKey },
    });
    if (existing) return existing; // duplicate submission — return the original
  }
  const run = await prisma.agentRun.create({
    data: {
      agentId: opts.agentId,
      projectId: opts.projectId,
      taskId: opts.taskId ?? null,
      objective: opts.objective,
      maxIterations: opts.maxIterations ?? 12,
      idempotencyKey: opts.idempotencyKey ?? null,
    },
  });
  await emitActivity({
    projectId: opts.projectId,
    actor: 'system',
    type: 'run_queued',
    summary: `Run queued for agent`,
    data: { runId: run.id, objective: opts.objective },
    refId: run.id,
  });
  return run;
}

/** Create a run and process it to a resting state (completed/failed/paused/…). */
export async function startRun(opts: StartRunOptions): Promise<AgentRun> {
  const run = await createRun(opts);
  return processRun(run.id);
}

/** Create a run and process it in the background. */
export async function startRunInBackground(opts: StartRunOptions): Promise<AgentRun> {
  const run = await createRun(opts);
  void processRun(run.id).catch((err) => console.error('[engine] background run failed', err));
  return run;
}

async function loadRun(runId: string) {
  const run = await prisma.agentRun.findUnique({
    where: { id: runId },
    include: {
      agent: { include: { modelConfig: true, workspace: true } },
      task: true,
      project: { include: { workspace: true } },
    },
  });
  if (!run) throw new Error(`Run not found: ${runId}`);
  return run;
}

type LoadedRun = Awaited<ReturnType<typeof loadRun>>;

/** Count trailing assistant messages with no tool calls (loop detection). */
function trailingTextOnlyTurns(transcript: NormMessage[]): number {
  let n = 0;
  for (let i = transcript.length - 1; i >= 0; i--) {
    const m = transcript[i]!;
    if (m.role === 'assistant') {
      if (m.toolCalls && m.toolCalls.length > 0) break;
      n++;
    } else if (m.role === 'tool') {
      break;
    }
  }
  return n;
}

async function budgetAllowanceUsd(run: LoadedRun): Promise<number> {
  const extraGrants = await prisma.approvalRequest.count({
    where: { runId: run.id, action: 'budget_increase', status: 'approved' },
  });
  return run.agent.maxCostPerRunUsd * (1 + extraGrants);
}

/**
 * Drive a run until it reaches a resting state. Safe to call repeatedly;
 * concurrent calls for the same run are no-ops.
 */
export async function processRun(runId: string): Promise<AgentRun> {
  if (activeRuns.has(runId)) return (await loadRun(runId)) as AgentRun;
  activeRuns.add(runId);
  try {
    for (;;) {
      const run = await loadRun(runId);

      if (run.status === 'queued') {
        await prisma.agentRun.update({
          where: { id: runId },
          data: { status: 'running', startedAt: run.startedAt ?? new Date() },
        });
        await prisma.agent.update({ where: { id: run.agentId }, data: { status: 'working' } });
        if (run.taskId) {
          await prisma.task.update({
            where: { id: run.taskId },
            data: { status: 'in_progress', ownerAgentId: run.task?.ownerAgentId ?? run.agentId },
          });
        }
        await emitActivity({
          projectId: run.projectId,
          actor: run.agentId,
          type: 'run_started',
          summary: `${run.agent.name} started: ${run.objective.slice(0, 100)}`,
          data: { runId, taskId: run.taskId },
          refId: runId,
        });
        continue;
      }
      if (run.status !== 'running') return run; // paused / awaiting_approval / terminal

      if (run.iterations >= run.maxIterations) {
        return failRun(run, `Maximum iterations (${run.maxIterations}) reached — stopping to prevent a loop.`);
      }

      const allowance = await budgetAllowanceUsd(run);
      if (run.costUsd >= allowance) {
        await prisma.approvalRequest.create({
          data: {
            projectId: run.projectId,
            runId: run.id,
            agentId: run.agentId,
            action: 'budget_increase',
            reason: `Run cost $${run.costUsd.toFixed(4)} reached the limit of $${allowance.toFixed(4)}. Approve to grant another $${run.agent.maxCostPerRunUsd.toFixed(2)}.`,
            payloadJson: toJson({ kind: 'budget', costUsd: run.costUsd, allowance }),
            riskLevel: 'medium',
          },
        });
        await setRunStatus(run, 'awaiting_approval');
        await notify({
          type: 'budget_warning',
          title: `${run.agent.name} hit its budget limit`,
          body: `Run paused at $${run.costUsd.toFixed(4)}. Approve a budget increase to continue.`,
          projectId: run.projectId,
        });
        return loadRun(runId);
      }

      const transcript = parseJson<NormMessage[]>(run.transcriptJson, []);
      const ctx = await assembleContext({
        workspace: run.project.workspace,
        project: run.project,
        agent: run.agent,
        task: run.task,
        objective: run.objective,
      });
      if (transcript.length === 0) {
        transcript.push({ role: 'user', content: ctx.taskPrompt });
      }

      const allowedTools = parseJson<string[]>(run.agent.toolsJson, []);
      const tools = toolDefsFor(allowedTools);
      const mc = run.agent.modelConfig;
      const startedAt = Date.now();

      let resp;
      try {
        resp = await callWithRetry(
          mc.provider,
          {
            modelId: mc.modelId,
            system: ctx.system,
            messages: transcript,
            tools,
            temperature: mc.temperature,
            maxTokens: mc.maxTokens,
            baseUrl: mc.baseUrl,
            apiKey: mc.apiKeyEnvVar ? process.env[mc.apiKeyEnvVar] : undefined,
          },
          async (attempt, err) => {
            await emitActivity({
              projectId: run.projectId,
              actor: 'system',
              type: 'retry',
              summary: `Provider ${mc.provider} failed (${err.kind}); retry ${attempt}`,
              data: { runId, attempt, error: err.message },
              refId: runId,
            });
          },
        );
      } catch (err) {
        const msg = err instanceof ProviderError ? `${err.kind}: ${err.message}` : String(err);
        await prisma.modelCall.create({
          data: {
            runId,
            projectId: run.projectId,
            agentId: run.agentId,
            seq: run.iterations,
            provider: mc.provider,
            modelId: mc.modelId,
            systemPrompt: ctx.system,
            messagesJson: toJson(transcript),
            toolDefsJson: toJson(tools),
            settingsJson: toJson({ temperature: mc.temperature, maxTokens: mc.maxTokens }),
            contextJson: toJson(ctx.contextManifest),
            status: 'error',
            error: msg,
            durationMs: Date.now() - startedAt,
          },
        });
        return failRun(run, `Provider error: ${msg}`);
      }

      const costUsd = computeCostUsd(resp.usage, mc);
      const modelCall = await prisma.modelCall.create({
        data: {
          runId,
          projectId: run.projectId,
          agentId: run.agentId,
          seq: run.iterations,
          provider: mc.provider,
          modelId: mc.modelId,
          systemPrompt: ctx.system,
          messagesJson: toJson(transcript),
          toolDefsJson: toJson(tools),
          settingsJson: toJson({ temperature: mc.temperature, maxTokens: mc.maxTokens }),
          contextJson: toJson(ctx.contextManifest),
          responseText: resp.text,
          toolCallsJson: toJson(resp.toolCalls),
          stopReason: resp.stopReason,
          inputTokens: resp.usage.inputTokens,
          outputTokens: resp.usage.outputTokens,
          costUsd,
          durationMs: Date.now() - startedAt,
        },
      });
      await prisma.usageRecord.create({
        data: {
          projectId: run.projectId,
          agentId: run.agentId,
          runId,
          modelCallId: modelCall.id,
          provider: mc.provider,
          modelId: mc.modelId,
          inputTokens: resp.usage.inputTokens,
          outputTokens: resp.usage.outputTokens,
          costUsd,
        },
      });
      await emitActivity({
        projectId: run.projectId,
        actor: run.agentId,
        type: 'model_call',
        summary: `${run.agent.name} ← ${mc.modelId}: ${resp.text.slice(0, 120) || '(tool use)'}`,
        data: {
          modelCallId: modelCall.id,
          runId,
          text: resp.text.slice(0, 500),
          toolCalls: resp.toolCalls.map((t) => t.name),
          usage: resp.usage,
          costUsd,
        },
        refId: modelCall.id,
      });

      transcript.push({ role: 'assistant', content: resp.text, toolCalls: resp.toolCalls });
      await prisma.agentRun.update({
        where: { id: runId },
        data: {
          transcriptJson: toJson(transcript),
          iterations: run.iterations + 1,
          inputTokens: run.inputTokens + resp.usage.inputTokens,
          outputTokens: run.outputTokens + resp.usage.outputTokens,
          costUsd: run.costUsd + costUsd,
        },
      });
      if (run.taskId && costUsd > 0) {
        await prisma.task.update({
          where: { id: run.taskId },
          data: { costUsd: { increment: costUsd } },
        });
      }

      if (resp.toolCalls.length > 0) {
        const outcome = await handleToolCalls(run, modelCall.id, transcript, resp.toolCalls, allowedTools);
        if (outcome === 'stop') return loadRun(runId);
        continue;
      }

      // No tool calls: allow one continuation nudge, then treat text as the result.
      if (trailingTextOnlyTurns(transcript) >= 2) {
        return finalizeRun(run, resp.text.slice(0, 1000) || 'Run finished without an explicit summary.');
      }
      transcript.push({
        role: 'user',
        content: 'Continue. Use your tools to make progress, and call complete_task with a summary when finished.',
      });
      await prisma.agentRun.update({
        where: { id: runId },
        data: { transcriptJson: toJson(transcript) },
      });
    }
  } finally {
    activeRuns.delete(runId);
  }
}

/**
 * Execute the model's tool calls in order. Returns 'stop' if the run reached a
 * resting state (approval gate or terminal tool), 'continue' otherwise.
 */
async function handleToolCalls(
  run: LoadedRun,
  modelCallId: string,
  transcript: NormMessage[],
  toolCalls: NormToolCall[],
  allowedTools: string[],
): Promise<'stop' | 'continue'> {
  const permissions = parseJson<AgentPermissions>(run.agent.permissionsJson, {});
  const toolCtx: ToolContext = {
    projectId: run.projectId,
    agentId: run.agentId,
    agentName: run.agent.name,
    runId: run.id,
    taskId: run.taskId,
    modelCallId,
    allowedTools,
  };

  const persistTranscript = () =>
    prisma.agentRun.update({ where: { id: run.id }, data: { transcriptJson: toJson(transcript) } });

  for (let i = 0; i < toolCalls.length; i++) {
    const tc = toolCalls[i]!;

    // Re-check status between tool calls so cancel/pause is honored mid-batch.
    const fresh = await prisma.agentRun.findUnique({ where: { id: run.id }, select: { status: true } });
    if (!fresh || !['running'].includes(fresh.status)) {
      await persistTranscript();
      return 'stop';
    }

    if (allowedTools.includes(tc.name) && toolNeedsApproval(tc.name, permissions)) {
      const spec = TOOL_SPECS[tc.name];
      await prisma.agentRun.update({
        where: { id: run.id },
        data: {
          pendingToolCallJson: toJson({ modelCallId, current: tc, remaining: toolCalls.slice(i + 1) }),
        },
      });
      await prisma.approvalRequest.create({
        data: {
          projectId: run.projectId,
          runId: run.id,
          agentId: run.agentId,
          action: `tool:${tc.name}`,
          reason: `${run.agent.name} wants to run ${tc.name}. Risk: ${spec?.risk ?? 'unknown'}.`,
          payloadJson: toJson({ toolName: tc.name, input: tc.input }),
          riskLevel: spec?.risk ?? 'medium',
        },
      });
      await setRunStatus(run, 'awaiting_approval');
      await notify({
        type: 'approval_request',
        title: `${run.agent.name} needs approval for ${tc.name}`,
        body: `Requested via run "${run.objective.slice(0, 80)}"`,
        projectId: run.projectId,
      });
      await persistTranscript();
      return 'stop';
    }

    const result = await executeTool(toolCtx, tc.name, tc.input);
    transcript.push({
      role: 'tool',
      toolCallId: tc.id,
      name: tc.name,
      content: toJson(result.ok ? result.output : { error: result.error }),
    });

    if (result.terminal) {
      await persistTranscript();
      await finalizeRun(run, result.terminal.summary);
      return 'stop';
    }
  }
  await persistTranscript();
  return 'continue';
}

async function setRunStatus(run: LoadedRun, status: string): Promise<void> {
  await prisma.agentRun.update({ where: { id: run.id }, data: { status } });
  const agentStatus =
    status === 'running' ? 'working'
    : status === 'awaiting_approval' ? 'awaiting_approval'
    : status === 'paused' ? 'paused'
    : 'idle';
  await prisma.agent.update({ where: { id: run.agentId }, data: { status: agentStatus } });
  await emitActivity({
    projectId: run.projectId,
    actor: 'system',
    type: 'status_change',
    summary: `Run for ${run.agent.name} → ${status}`,
    data: { runId: run.id, status },
    refId: run.id,
  });
}

async function failRun(run: LoadedRun, error: string): Promise<AgentRun> {
  const updated = await prisma.agentRun.update({
    where: { id: run.id },
    data: { status: 'failed', error, finishedAt: new Date() },
  });
  await prisma.agent.update({ where: { id: run.agentId }, data: { status: 'error' } });
  if (run.taskId) {
    await prisma.task.update({ where: { id: run.taskId }, data: { status: 'failed' } });
  }
  await emitActivity({
    projectId: run.projectId,
    actor: 'system',
    type: 'error',
    summary: `Run failed for ${run.agent.name}: ${error.slice(0, 150)}`,
    data: { runId: run.id, error },
    refId: run.id,
  });
  await notify({
    type: 'agent_failure',
    title: `${run.agent.name} run failed`,
    body: error.slice(0, 300),
    projectId: run.projectId,
  });
  return updated;
}

async function finalizeRun(run: LoadedRun, summary: string): Promise<AgentRun> {
  const updated = await prisma.agentRun.update({
    where: { id: run.id },
    data: { status: 'completed', resultSummary: summary, finishedAt: new Date() },
  });
  await prisma.agent.update({ where: { id: run.agentId }, data: { status: 'idle' } });

  if (run.taskId && run.task) {
    const task = await prisma.task.findUnique({ where: { id: run.taskId } });
    if (task) {
      const isOwner = task.ownerAgentId === run.agentId || task.ownerAgentId == null;
      const isReviewer = task.reviewerAgentId === run.agentId;
      let newStatus = task.status;
      if (isOwner && !isReviewer) {
        newStatus = task.reviewerAgentId ? 'awaiting_review' : 'completed';
      } else if (isReviewer) {
        // Review verdict: "changes requested" sends the task back to its owner.
        newStatus = /changes requested/i.test(summary) ? 'in_progress' : 'completed';
      }
      await prisma.task.update({
        where: { id: task.id },
        data: { status: newStatus, resultSummary: summary },
      });
      if (newStatus === 'awaiting_review' && task.reviewerAgentId) {
        await notify({
          type: 'review_request',
          title: `Task "${task.title}" awaits review`,
          projectId: run.projectId,
        });
      }
      if (newStatus === 'completed') {
        await notify({
          type: 'task_completed',
          title: `Task completed: ${task.title}`,
          body: summary.slice(0, 300),
          projectId: run.projectId,
        });
      }
    }
  }

  await emitActivity({
    projectId: run.projectId,
    actor: run.agentId,
    type: 'run_completed',
    summary: `${run.agent.name} finished: ${summary.slice(0, 150)}`,
    data: { runId: run.id, summary, costUsd: updated.costUsd, iterations: updated.iterations },
    refId: run.id,
  });
  return updated;
}

// ---------------------------------------------------------------------------
// User controls
// ---------------------------------------------------------------------------

export async function pauseRun(runId: string): Promise<AgentRun> {
  const run = await loadRun(runId);
  if (!['queued', 'running'].includes(run.status)) return run;
  await setRunStatus(run, 'paused');
  return loadRun(runId);
}

export async function resumeRun(runId: string): Promise<AgentRun> {
  const run = await loadRun(runId);
  if (!['paused', 'interrupted'].includes(run.status)) return run;
  await setRunStatus(run, 'running');
  void processRun(runId).catch((err) => console.error('[engine] resume failed', err));
  return loadRun(runId);
}

export async function cancelRun(runId: string): Promise<AgentRun> {
  const run = await loadRun(runId);
  if (['completed', 'failed', 'cancelled'].includes(run.status)) return run;
  const updated = await prisma.agentRun.update({
    where: { id: runId },
    data: { status: 'cancelled', finishedAt: new Date() },
  });
  await prisma.agent.update({ where: { id: run.agentId }, data: { status: 'idle' } });
  if (run.taskId) {
    await prisma.task.update({ where: { id: run.taskId }, data: { status: 'cancelled' } });
  }
  await emitActivity({
    projectId: run.projectId,
    actor: 'user',
    type: 'run_cancelled',
    summary: `Run for ${run.agent.name} cancelled by user`,
    data: { runId },
    refId: runId,
  });
  return updated;
}

/** Pause every active run in a project (or the whole workspace). */
export async function pauseAll(projectId?: string): Promise<number> {
  const runs = await prisma.agentRun.findMany({
    where: { status: { in: ['queued', 'running'] }, ...(projectId ? { projectId } : {}) },
    select: { id: true },
  });
  for (const r of runs) await pauseRun(r.id);
  return runs.length;
}

/**
 * Resolve an approval request. Approving a pending tool call executes it and
 * resumes the run; rejecting feeds the rejection back to the agent so it can
 * adapt.
 */
export async function resolveApproval(
  approvalId: string,
  decision: 'approved' | 'rejected',
  note = '',
): Promise<void> {
  const approval = await prisma.approvalRequest.findUnique({ where: { id: approvalId } });
  if (!approval || approval.status !== 'pending') throw new Error('Approval not found or already resolved');

  await prisma.approvalRequest.update({
    where: { id: approvalId },
    data: { status: decision, resolution: note, resolvedBy: 'user', resolvedAt: new Date() },
  });
  await emitActivity({
    projectId: approval.projectId,
    actor: 'user',
    type: 'approval',
    summary: `User ${decision} "${approval.action}"${note ? ` — ${note}` : ''}`,
    data: { approvalId, decision, note },
    refId: approvalId,
  });

  if (!approval.runId) return;
  const run = await loadRun(approval.runId);
  if (run.status !== 'awaiting_approval') return;

  const pending = parseJson<{
    modelCallId: string;
    current: NormToolCall;
    remaining: NormToolCall[];
  } | null>(run.pendingToolCallJson, null);

  const transcript = parseJson<NormMessage[]>(run.transcriptJson, []);
  const allowedTools = parseJson<string[]>(run.agent.toolsJson, []);

  if (approval.action === 'budget_increase') {
    if (decision === 'approved') {
      await prisma.agentRun.update({ where: { id: run.id }, data: { status: 'running' } });
      void processRun(run.id).catch((err) => console.error('[engine] post-approval resume failed', err));
    } else {
      await cancelRun(run.id);
    }
    return;
  }

  if (pending) {
    const toolCtx: ToolContext = {
      projectId: run.projectId,
      agentId: run.agentId,
      agentName: run.agent.name,
      runId: run.id,
      taskId: run.taskId,
      modelCallId: pending.modelCallId,
      allowedTools,
    };
    let terminalSummary: string | null = null;

    if (decision === 'approved') {
      const result = await executeTool(toolCtx, pending.current.name, pending.current.input);
      transcript.push({
        role: 'tool',
        toolCallId: pending.current.id,
        name: pending.current.name,
        content: toJson(result.ok ? result.output : { error: result.error }),
      });
      if (result.terminal) terminalSummary = result.terminal.summary;
    } else {
      transcript.push({
        role: 'tool',
        toolCallId: pending.current.id,
        name: pending.current.name,
        content: toJson({ error: `Rejected by the user${note ? `: ${note}` : ''}. Do not retry this exact action.` }),
      });
      await prisma.toolCall.create({
        data: {
          runId: run.id,
          modelCallId: pending.modelCallId,
          projectId: run.projectId,
          agentId: run.agentId,
          toolName: pending.current.name,
          inputJson: toJson(pending.current.input),
          outputJson: toJson(null),
          status: 'rejected',
          error: note || 'Rejected by user',
          riskLevel: TOOL_SPECS[pending.current.name]?.risk ?? 'medium',
        },
      });
    }

    // Feed any remaining queued tool calls a skip notice; the model can re-issue them.
    for (const rem of pending.remaining) {
      transcript.push({
        role: 'tool',
        toolCallId: rem.id,
        name: rem.name,
        content: toJson({ error: 'Skipped while awaiting approval — re-issue this call if still needed.' }),
      });
    }

    await prisma.agentRun.update({
      where: { id: run.id },
      data: {
        transcriptJson: toJson(transcript),
        pendingToolCallJson: null,
        status: terminalSummary ? run.status : 'running',
      },
    });

    if (terminalSummary) {
      await finalizeRun(run, terminalSummary);
    } else {
      await prisma.agent.update({ where: { id: run.agentId }, data: { status: 'working' } });
      void processRun(run.id).catch((err) => console.error('[engine] post-approval resume failed', err));
    }
  }
}

/** Mark runs left "running" by a dead process as interrupted (called at boot). */
export async function recoverInterruptedRuns(): Promise<number> {
  const stale = await prisma.agentRun.findMany({
    where: { status: 'running' },
    select: { id: true, projectId: true },
  });
  const staleIds = stale.filter((r) => !activeRuns.has(r.id)).map((r) => r.id);
  if (staleIds.length === 0) return 0;
  await prisma.agentRun.updateMany({
    where: { id: { in: staleIds } },
    data: { status: 'interrupted' },
  });
  for (const r of stale.filter((s) => staleIds.includes(s.id))) {
    await emitActivity({
      projectId: r.projectId,
      actor: 'system',
      type: 'status_change',
      summary: 'Run interrupted by application restart — resume it from the run panel.',
      data: { runId: r.id, status: 'interrupted' },
      refId: r.id,
    });
  }
  return staleIds.length;
}
