import type { Agent, AgentRun, ModelConfig, Project, ProjectRun } from '@prisma/client';
import { prisma } from '../db';
import { emitActivity, notify } from '../events';
import { parseJson, toJson } from '../json';
import { callWithRetry, computeCostUsd } from '../providers/registry';
import { ProviderError } from '../providers/types';
import { AgentOutput, AgentOutputSchema, buildPhasePrompt, buildRepairPrompt, extractJson } from './prompts';

/**
 * Collaboration orchestrator — the reactive layer that was missing.
 *
 * A ProjectRun is a durable state machine:
 *
 *   independent_analysis → cross_review → synthesis → verification
 *        ↑                                                │
 *        └──────────── revision ←── needs_revision ───────┘
 *   verification --approved--> finalizing → done
 *
 * advanceProjectRun() is the single transition function. It is called after
 * every meaningful event (start, step success, step failure, resume, retry,
 * boot recovery), performs exactly the work the persisted state says is
 * missing, transitions atomically (guarded updates), and re-enters until the
 * run rests. Steps are recorded in a slot map *before* execution, so repeated
 * events can never duplicate a provider call; completed steps are always
 * reused, never re-run.
 */

// ---------------------------------------------------------------------------
// Types & helpers
// ---------------------------------------------------------------------------

const g = globalThis as unknown as { __activeProjectRuns?: Set<string> };
const active: Set<string> = g.__activeProjectRuns ?? new Set();
g.__activeProjectRuns = active;

type Steps = Record<string, string>; // slot key → AgentRun id

class StepFailed extends Error {
  constructor(message: string, public readonly agentRunId: string | null) {
    super(message);
  }
}

type LoadedProjectRun = ProjectRun & { project: Project };
type LoadedAgent = Agent & { modelConfig: ModelConfig };

async function loadProjectRun(id: string): Promise<LoadedProjectRun> {
  const run = await prisma.projectRun.findUnique({ where: { id }, include: { project: true } });
  if (!run) throw new Error(`ProjectRun not found: ${id}`);
  return run;
}

async function loadAgent(id: string): Promise<LoadedAgent> {
  return prisma.agent.findUniqueOrThrow({ where: { id }, include: { modelConfig: true } });
}

/**
 * Atomic guarded transition. Returns false if another worker already moved
 * the run past `fromPhase`/`fromStatus` (safe concurrent exit).
 */
async function transition(
  id: string,
  from: { phase: string; status?: string },
  to: { phase?: string; status?: string; iteration?: number; failureReason?: string | null; finalOutput?: string; completedAt?: Date; startedAt?: Date },
  summary: string,
  eventType = 'status_change',
): Promise<boolean> {
  const res = await prisma.projectRun.updateMany({
    where: { id, phase: from.phase, ...(from.status ? { status: from.status } : {}) },
    data: { ...to, lastTransitionAt: new Date() },
  });
  if (res.count === 0) {
    console.warn(`[collab] transition skipped (already advanced): ${id} ${from.phase} → ${to.phase ?? to.status}`);
    return false;
  }
  const run = await loadProjectRun(id);
  await emitActivity({
    projectId: run.projectId,
    actor: 'system',
    type: eventType,
    summary,
    data: { projectRunId: id, phase: run.phase, status: run.status, iteration: run.iteration },
    refId: id,
  });
  console.log(`[collab] advanced`, { projectRunId: id, from, to, summary });
  return true;
}

function stepOutput(run: AgentRun | null): AgentOutput | null {
  if (!run?.parsedOutputJson) return null;
  const parsed = AgentOutputSchema.safeParse(parseJson(run.parsedOutputJson, null));
  return parsed.success ? parsed.data : null;
}

async function loadStep(steps: Steps, key: string): Promise<{ run: AgentRun; output: AgentOutput } | null> {
  const id = steps[key];
  if (!id) return null;
  const run = await prisma.agentRun.findUnique({ where: { id } });
  if (!run || run.status !== 'completed') return null;
  const output = stepOutput(run);
  return output ? { run, output } : null;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export interface StartCollaborationOptions {
  prompt: string;
  maxIterations?: number;
  maxModelCalls?: number;
  idempotencyKey?: string;
}

/** Entry point: user submits a prompt → durable ProjectRun + umbrella task. */
export async function startCollaboration(projectId: string, opts: StartCollaborationOptions): Promise<ProjectRun> {
  if (opts.idempotencyKey) {
    const existing = await prisma.projectRun.findUnique({ where: { idempotencyKey: opts.idempotencyKey } });
    if (existing) return existing;
  }
  const links = await prisma.projectAgent.findMany({
    where: { projectId },
    include: { agent: true },
    orderBy: { assignedAt: 'asc' },
  });
  if (links.length < 2) throw new Error('Collaboration needs at least 2 agents assigned to the project');
  const [a, b] = [links[0]!.agent, links[1]!.agent];
  // Synthesizer: designated lead (manager/lead role) among the pair, else Agent A.
  const lead = [a, b].find((x) => /manager|lead/i.test(x.role));
  const synthesizer = lead ?? a;

  const task = await prisma.task.create({
    data: {
      projectId,
      title: `Collaborate: ${opts.prompt.slice(0, 80)}`,
      description: opts.prompt,
      status: 'in_progress',
      priority: 'high',
      ownerAgentId: synthesizer.id,
      createdBy: 'user',
    },
  });
  const run = await prisma.projectRun.create({
    data: {
      projectId,
      taskId: task.id,
      prompt: opts.prompt,
      maxIterations: opts.maxIterations ?? 3,
      maxModelCalls: opts.maxModelCalls ?? 10,
      agentAId: a.id,
      agentBId: b.id,
      synthesizerId: synthesizer.id,
      idempotencyKey: opts.idempotencyKey ?? null,
      startedAt: new Date(),
    },
  });
  await emitActivity({
    projectId,
    actor: 'user',
    type: 'project_run_started',
    summary: `Collaboration started: ${a.name} + ${b.name} (synthesizer: ${synthesizer.name})`,
    data: { projectRunId: run.id, prompt: opts.prompt.slice(0, 300), agentA: a.name, agentB: b.name },
    refId: run.id,
  });
  await emitActivity({
    projectId,
    actor: 'user',
    type: 'task_created',
    summary: `Task created for collaboration: "${task.title}"`,
    data: { taskId: task.id, projectRunId: run.id },
    refId: task.id,
  });
  scheduleAdvance(run.id);
  return run;
}

/** Fire-and-forget scheduling of the next advance (in-process queue). */
export function scheduleAdvance(projectRunId: string): void {
  setImmediate(() => {
    void advanceProjectRun(projectRunId).catch((err) =>
      console.error('[collab] advance failed', { projectRunId, err: String(err) }),
    );
  });
}

/**
 * The single state-transition function. Idempotent and safe to call from
 * anywhere, any number of times.
 */
export async function advanceProjectRun(projectRunId: string): Promise<ProjectRun> {
  if (active.has(projectRunId)) return loadProjectRun(projectRunId); // another worker in this process is advancing
  active.add(projectRunId);
  try {
    for (;;) {
      const run = await loadProjectRun(projectRunId);

      if (run.status === 'created') {
        await transition(run.id, { phase: run.phase, status: 'created' }, { status: 'running', startedAt: run.startedAt ?? new Date() },
          'Collaboration run entering independent analysis', 'status_change');
        continue;
      }
      if (run.status !== 'running') {
        logNoTransition(run, 'run is not in a runnable state');
        return run;
      }

      // Global model-call budget for this collaboration.
      const calls = await prisma.modelCall.count({ where: { run: { projectRunId: run.id } } });
      if (calls >= run.maxModelCalls) {
        await failRun(run, `Model call limit reached (${calls}/${run.maxModelCalls}). Increase maxModelCalls and retry.`);
        return loadProjectRun(projectRunId);
      }

      try {
        const done = await advancePhase(run);
        if (done) return loadProjectRun(projectRunId);
      } catch (err) {
        if (err instanceof StepFailed) {
          await failRun(await loadProjectRun(projectRunId), err.message, err.agentRunId);
          return loadProjectRun(projectRunId);
        }
        throw err;
      }
    }
  } finally {
    active.delete(projectRunId);
  }
}

/** Returns true when the run reached a resting state (caller should stop looping). */
async function advancePhase(run: LoadedProjectRun): Promise<boolean> {
  const steps = parseJson<Steps>(run.stepsJson, {});
  const [agentA, agentB, synthesizer] = await Promise.all([
    loadAgent(run.agentAId),
    loadAgent(run.agentBId),
    loadAgent(run.synthesizerId),
  ]);
  const verifier = synthesizer.id === agentA.id ? agentB : agentA;
  const base = {
    originalPrompt: run.prompt,
    projectName: run.project.name,
    projectInstructions: run.project.instructions,
  };

  switch (run.phase) {
    case 'independent_analysis': {
      const aStep =
        (await loadStep(steps, 'initial:A')) ??
        (await runStep(run, 'initial:A', agentA, 'initial', buildPhasePrompt({ ...base, phase: 'initial', agentName: agentA.name }), null));
      const bStep =
        (await loadStep(steps, 'initial:B')) ??
        (await runStep(run, 'initial:B', agentB, 'initial', buildPhasePrompt({ ...base, phase: 'initial', agentName: agentB.name }), null));
      void aStep; void bStep;
      await transition(run.id, { phase: 'independent_analysis', status: 'running' }, { phase: 'cross_review' },
        'Both independent responses received — entering cross review', 'review_requested');
      return false;
    }

    case 'cross_review': {
      const initialA = await loadStep(steps, 'initial:A');
      const initialB = await loadStep(steps, 'initial:B');
      if (!initialA || !initialB) throw new StepFailed('Cross review reached without both initial outputs', null);

      if (!(await loadStep(steps, 'review:A'))) {
        await runStep(run, 'review:A', agentA, 'review',
          buildPhasePrompt({ ...base, phase: 'review', agentName: agentA.name, otherAgentName: agentB.name, otherAgentOutput: initialB.output.workProduct }),
          initialB.run.id);
      }
      if (!(await loadStep(steps, 'review:B'))) {
        await runStep(run, 'review:B', agentB, 'review',
          buildPhasePrompt({ ...base, phase: 'review', agentName: agentB.name, otherAgentName: agentA.name, otherAgentOutput: initialA.output.workProduct }),
          initialA.run.id);
      }
      await transition(run.id, { phase: 'cross_review', status: 'running' }, { phase: 'synthesis' },
        'Cross reviews complete — entering synthesis', 'review_completed');
      return false;
    }

    case 'synthesis': {
      if (!(await loadStep(steps, 'synthesis:0'))) {
        const [initialA, initialB, reviewA, reviewB] = await Promise.all([
          loadStep(steps, 'initial:A'), loadStep(steps, 'initial:B'),
          loadStep(steps, 'review:A'), loadStep(steps, 'review:B'),
        ]);
        await runStep(run, 'synthesis:0', synthesizer, 'synthesis',
          buildPhasePrompt({
            ...base, phase: 'synthesis', agentName: synthesizer.name,
            agentAName: agentA.name, agentAOutput: initialA?.output.workProduct,
            agentBName: agentB.name, agentBOutput: initialB?.output.workProduct,
            agentAReview: reviewA?.output.workProduct, agentBReview: reviewB?.output.workProduct,
          }),
          null);
      }
      await transition(run.id, { phase: 'synthesis', status: 'running' }, { phase: 'verification' },
        `Synthesis complete — ${verifier.name} verifying`, 'review_requested');
      return false;
    }

    case 'verification': {
      const slot = `verification:${run.iteration}`;
      const producedKey = run.iteration === 0 ? 'synthesis:0' : `revision:${run.iteration}`;
      const produced = await loadStep(steps, producedKey);
      if (!produced) throw new StepFailed(`Verification reached without a synthesized result (${producedKey})`, null);

      const verification =
        (await loadStep(steps, slot)) ??
        (await runStep(run, slot, verifier, 'verification',
          buildPhasePrompt({ ...base, phase: 'verification', agentName: verifier.name, previousOutput: produced.output.workProduct, iteration: run.iteration }),
          produced.run.id));

      const verdict = verification.output;
      if (verdict.status === 'completed' || verdict.nextAction === 'finalize') {
        await transition(run.id, { phase: 'verification', status: 'running' }, { phase: 'finalizing' },
          `${verifier.name} approved the result — finalizing`, 'project_finalizing');
        return false;
      }
      if (verdict.status === 'blocked' || verdict.status === 'needs_user_input') {
        await transition(run.id, { phase: 'verification', status: 'running' },
          { status: 'paused', failureReason: `${verifier.name} needs input: ${[...verdict.questions, ...verdict.issues].join(' | ').slice(0, 500)}` },
          `Collaboration paused — ${verifier.name} needs user input`, 'status_change');
        await notify({
          type: 'blocked',
          title: 'Collaboration needs your input',
          body: verdict.questions.join(' | ').slice(0, 300) || 'The verifying agent is blocked.',
          projectId: run.projectId,
        });
        return true;
      }
      // needs_revision (or anything else): revise if budget allows.
      if (run.iteration >= run.maxIterations) {
        await transition(run.id, { phase: 'verification', status: 'running' },
          { phase: 'finalizing', failureReason: `Revision limit reached (${run.maxIterations}); finalizing latest result without approval` },
          `Revision limit reached (${run.maxIterations}) — finalizing latest result with reviewer reservations`, 'project_finalizing');
        return false;
      }
      await transition(run.id, { phase: 'verification', status: 'running' },
        { phase: 'revision', iteration: run.iteration + 1 },
        `${verifier.name} requested changes (${verdict.issues.length} issue${verdict.issues.length === 1 ? '' : 's'}) — revision cycle ${run.iteration + 1}`,
        'revision_requested');
      return false;
    }

    case 'revision': {
      const slot = `revision:${run.iteration}`;
      if (!(await loadStep(steps, slot))) {
        const prevKey = run.iteration <= 1 ? 'synthesis:0' : `revision:${run.iteration - 1}`;
        const [previous, verification] = await Promise.all([
          loadStep(steps, prevKey),
          loadStep(steps, `verification:${run.iteration - 1}`),
        ]);
        if (!previous || !verification) throw new StepFailed('Revision reached without prior output/review', null);
        await runStep(run, slot, synthesizer, 'revision',
          buildPhasePrompt({
            ...base, phase: 'revision', agentName: synthesizer.name,
            previousOutput: previous.output.workProduct,
            reviewOutput: `Issues: ${verification.output.issues.join('; ')}\n${verification.output.workProduct}`,
          }),
          verification.run.id);
      }
      await transition(run.id, { phase: 'revision', status: 'running' }, { phase: 'verification' },
        `Revision ${run.iteration} produced — re-verifying`, 'review_requested');
      return false;
    }

    case 'finalizing': {
      await finalize(run, parseJson<Steps>(run.stepsJson, {}));
      return true;
    }

    case 'done':
      return true;

    default:
      logNoTransition(run, `unknown phase "${run.phase}"`);
      await failRun(run, `Unknown orchestration phase: ${run.phase}`);
      return true;
  }
}

// ---------------------------------------------------------------------------
// Step execution
// ---------------------------------------------------------------------------

/**
 * Execute one collaboration step as an AgentRun with full context visibility:
 * the exact prompts, provider, usage, cost, parse/validation results and the
 * parent run that caused it are all persisted.
 */
async function runStep(
  projectRun: LoadedProjectRun,
  slot: string,
  agent: LoadedAgent,
  runType: string,
  prompts: { system: string; user: string },
  parentAgentRunId: string | null,
): Promise<{ run: AgentRun; output: AgentOutput }> {
  const agentRun = await prisma.agentRun.create({
    data: {
      projectId: projectRun.projectId,
      agentId: agent.id,
      taskId: projectRun.taskId,
      projectRunId: projectRun.id,
      runType,
      parentAgentRunId,
      objective: `[${runType}] ${projectRun.prompt.slice(0, 120)}`,
      status: 'running',
      startedAt: new Date(),
      maxIterations: 2,
    },
  });
  // Claim the slot BEFORE calling the provider: a duplicate event replaying
  // this phase will find the slot occupied and never double-call the model.
  await prisma.projectRun.update({
    where: { id: projectRun.id },
    data: { stepsJson: toJson({ ...parseJson<Steps>((await loadProjectRun(projectRun.id)).stepsJson, {}), [slot]: agentRun.id }) },
  });
  await prisma.agent.update({ where: { id: agent.id }, data: { status: 'working' } });
  await emitActivity({
    projectId: projectRun.projectId,
    actor: agent.id,
    type: 'agent_run_started',
    summary: `${agent.name} started ${runType} step`,
    data: { projectRunId: projectRun.id, agentRunId: agentRun.id, slot, runType, parentAgentRunId },
    refId: agentRun.id,
  });

  const mc = agent.modelConfig;
  const settle = async (data: Partial<Parameters<typeof prisma.agentRun.update>[0]['data']>) =>
    prisma.agentRun.update({ where: { id: agentRun.id }, data: { finishedAt: new Date(), ...data } });

  const callModel = async (
    system: string,
    messages: Array<{ role: 'user' | 'assistant'; content: string }>,
    seq: number,
    note: string,
  ) => {
    const startedAt = Date.now();
    try {
      const resp = await callWithRetry(mc.provider, {
        modelId: mc.modelId,
        system,
        messages,
        tools: [],
        temperature: mc.temperature,
        maxTokens: mc.maxTokens,
        baseUrl: mc.baseUrl,
        apiKey: mc.apiKeyEnvVar ? process.env[mc.apiKeyEnvVar] : undefined,
      });
      const costUsd = computeCostUsd(resp.usage, mc);
      const call = await prisma.modelCall.create({
        data: {
          runId: agentRun.id,
          projectId: projectRun.projectId,
          agentId: agent.id,
          seq,
          provider: mc.provider,
          modelId: mc.modelId,
          systemPrompt: system,
          messagesJson: toJson(messages),
          settingsJson: toJson({ temperature: mc.temperature, maxTokens: mc.maxTokens }),
          contextJson: toJson({
            collaboration: { projectRunId: projectRun.id, phase: projectRun.phase, slot, runType, parentAgentRunId, note },
          }),
          responseText: resp.text,
          stopReason: resp.stopReason,
          inputTokens: resp.usage.inputTokens,
          outputTokens: resp.usage.outputTokens,
          costUsd,
          durationMs: Date.now() - startedAt,
        },
      });
      await prisma.usageRecord.create({
        data: {
          projectId: projectRun.projectId, agentId: agent.id, runId: agentRun.id, modelCallId: call.id,
          provider: mc.provider, modelId: mc.modelId,
          inputTokens: resp.usage.inputTokens, outputTokens: resp.usage.outputTokens, costUsd,
        },
      });
      await prisma.agentRun.update({
        where: { id: agentRun.id },
        data: {
          iterations: { increment: 1 },
          inputTokens: { increment: resp.usage.inputTokens },
          outputTokens: { increment: resp.usage.outputTokens },
          costUsd: { increment: costUsd },
        },
      });
      await emitActivity({
        projectId: projectRun.projectId,
        actor: agent.id,
        type: 'model_call',
        summary: `${agent.name} ← ${mc.modelId} (${runType}${note ? `, ${note}` : ''})`,
        data: { modelCallId: call.id, runId: agentRun.id, projectRunId: projectRun.id, usage: resp.usage, costUsd },
        refId: call.id,
      });
      return { text: resp.text, stopReason: resp.stopReason };
    } catch (err) {
      const msg = err instanceof ProviderError ? `${err.kind}: ${err.message}` : String(err);
      await prisma.modelCall.create({
        data: {
          runId: agentRun.id, projectId: projectRun.projectId, agentId: agent.id, seq,
          provider: mc.provider, modelId: mc.modelId, systemPrompt: system,
          messagesJson: toJson(messages),
          contextJson: toJson({ collaboration: { projectRunId: projectRun.id, slot, note } }),
          status: 'error', error: msg, durationMs: Date.now() - startedAt,
        },
      });
      throw new StepFailed(`${agent.name} ${runType} step failed — provider error: ${msg}`, agentRun.id);
    }
  };

  const MAX_CONTINUATIONS = 2;
  const isTruncated = (stopReason: string) => ['max_tokens', 'length'].includes(stopReason);

  try {
    // Long outputs (e.g. a full synthesis) can hit the model's output-token
    // cap mid-JSON. Detect the truncated stop reason and ask the model to
    // continue where it stopped, stitching the pieces together.
    let { text: raw, stopReason } = await callModel(
      prompts.system,
      [{ role: 'user', content: prompts.user }],
      0,
      '',
    );
    let seq = 0;
    while (isTruncated(stopReason) && seq < MAX_CONTINUATIONS) {
      seq++;
      const cont = await callModel(
        prompts.system,
        [
          { role: 'user', content: prompts.user },
          { role: 'assistant', content: raw },
          {
            role: 'user',
            content:
              'Your previous response was cut off before it finished. Continue EXACTLY where it stopped — output only the remaining text, with no repetition and no preamble.',
          },
        ],
        seq,
        `continuation ${seq} (previous response truncated)`,
      );
      raw += cont.text;
      stopReason = cont.stopReason;
    }

    let parsed = AgentOutputSchema.safeParse(tryParse(extractJson(raw)));
    let finalRaw = raw;
    if (!parsed.success && !isTruncated(stopReason)) {
      // One repair pass, then fail visibly with the raw response preserved.
      // (Skipped when the response is still truncated — regenerating under
      // the same output cap cannot succeed; that needs a bigger maxTokens.)
      const repair = buildRepairPrompt(raw);
      const repaired = await callModel(repair.system, [{ role: 'user', content: repair.user }], seq + 1, 'JSON repair pass');
      finalRaw = repaired.text;
      parsed = AgentOutputSchema.safeParse(tryParse(extractJson(finalRaw)));
    }
    if (!parsed.success) {
      const truncationHint = isTruncated(stopReason)
        ? ` The response was still truncated after ${MAX_CONTINUATIONS} continuations (stop reason: ${stopReason}) — increase maxTokens on the "${mc.name}" model configuration.`
        : '';
      await settle({
        status: 'failed',
        error: `schema_validation_failed: agent output did not match AgentOutputSchema after one repair attempt (${parsed.error.issues[0]?.message ?? 'invalid'}).${truncationHint}`,
        transcriptJson: toJson([{ role: 'assistant', content: finalRaw }]),
      });
      await prisma.agent.update({ where: { id: agent.id }, data: { status: 'error' } });
      await emitActivity({
        projectId: projectRun.projectId, actor: 'system', type: 'error',
        summary: `${agent.name} ${runType} output failed schema validation (raw response preserved on the run)`,
        data: { projectRunId: projectRun.id, agentRunId: agentRun.id, slot, raw: finalRaw.slice(0, 800) },
        refId: agentRun.id,
      });
      throw new StepFailed(`${agent.name} ${runType} output failed schema validation after repair attempt`, agentRun.id);
    }

    const output = parsed.data;
    const updated = await settle({
      status: 'completed',
      resultSummary: output.summary,
      parsedOutputJson: toJson(output),
      transcriptJson: toJson([
        { role: 'user', content: prompts.user },
        { role: 'assistant', content: finalRaw },
      ]),
    });
    await prisma.agent.update({ where: { id: agent.id }, data: { status: 'idle' } });
    await emitActivity({
      projectId: projectRun.projectId,
      actor: agent.id,
      type: 'agent_run_completed',
      summary: `${agent.name} completed ${runType}: ${output.summary.slice(0, 140)}`,
      data: {
        projectRunId: projectRun.id, agentRunId: agentRun.id, slot, runType,
        status: output.status, nextAction: output.nextAction, issues: output.issues,
      },
      refId: agentRun.id,
    });
    // Mirror reviews/verdicts into the Conversations view.
    if (runType === 'review' || runType === 'verification') {
      const parent = parentAgentRunId ? await prisma.agentRun.findUnique({ where: { id: parentAgentRunId } }) : null;
      await prisma.message.create({
        data: {
          projectId: projectRun.projectId,
          taskId: projectRun.taskId,
          fromAgentId: agent.id,
          toAgentId: parent?.agentId ?? null,
          type: 'review_result',
          content: `[${runType}] ${output.summary}${output.issues.length ? `\nIssues: ${output.issues.join('; ')}` : ''}`,
          runId: agentRun.id,
        },
      });
    }
    return { run: updated, output };
  } catch (err) {
    if (err instanceof StepFailed) {
      const current = await prisma.agentRun.findUnique({ where: { id: agentRun.id } });
      if (current && current.status === 'running') {
        await settle({ status: 'failed', error: err.message });
        await prisma.agent.update({ where: { id: agent.id }, data: { status: 'error' } });
      }
      throw err;
    }
    await settle({ status: 'failed', error: String(err) });
    await prisma.agent.update({ where: { id: agent.id }, data: { status: 'error' } });
    throw new StepFailed(`${agent.name} ${runType} step failed: ${String(err)}`, agentRun.id);
  }
}

function tryParse(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Terminal states, controls, recovery
// ---------------------------------------------------------------------------

async function finalize(run: LoadedProjectRun, steps: Steps): Promise<void> {
  // Latest work product: last approved revision, else the synthesis.
  let final: { run: AgentRun; output: AgentOutput } | null = null;
  for (let i = run.iteration; i >= 1 && !final; i--) final = await loadStep(steps, `revision:${i}`);
  final = final ?? (await loadStep(steps, 'synthesis:0'));
  if (!final) {
    await failRun(run, 'Finalization reached without any synthesized output');
    return;
  }
  const reservations = run.failureReason ? `\n\n> ⚠ ${run.failureReason}\n` : '';
  const body = `# Collaboration result\n\n**Prompt:** ${run.prompt}\n${reservations}\n${final.output.workProduct}\n`;

  // Persist the final result as a versioned project artifact.
  const path = `results/collaboration-${run.id.slice(-8)}.md`;
  const file = await prisma.projectFile.upsert({
    where: { projectId_path: { projectId: run.projectId, path } },
    update: { latestVersion: { increment: 1 }, deleted: false },
    create: { projectId: run.projectId, path, latestVersion: 1 },
  });
  await prisma.fileVersion.create({
    data: { fileId: file.id, version: file.latestVersion, content: body, authorAgentId: final.run.agentId, runId: final.run.id, note: 'Final collaboration result' },
  });

  const ok = await transition(run.id, { phase: 'finalizing', status: 'running' },
    { phase: 'done', status: 'completed', finalOutput: final.output.workProduct, completedAt: new Date() },
    `Collaboration completed — final result saved to ${path}`, 'project_completed');
  if (!ok) return;

  if (run.taskId) {
    await prisma.task.update({
      where: { id: run.taskId },
      data: { status: 'completed', resultSummary: final.output.summary },
    });
  }
  await prisma.message.create({
    data: {
      projectId: run.projectId,
      taskId: run.taskId,
      fromAgentId: final.run.agentId,
      type: 'completion_report',
      content: `Final collaboration result: ${final.output.summary}`,
      runId: final.run.id,
    },
  });
  await notify({
    type: 'project_completed',
    title: 'Collaboration completed',
    body: final.output.summary.slice(0, 300),
    projectId: run.projectId,
  });
}

async function failRun(run: LoadedProjectRun, reason: string, agentRunId?: string | null): Promise<void> {
  const res = await prisma.projectRun.updateMany({
    where: { id: run.id, status: { in: ['created', 'running'] } },
    data: { status: 'failed', failureReason: reason, lastTransitionAt: new Date(), completedAt: new Date() },
  });
  if (res.count === 0) return;
  if (run.taskId) await prisma.task.update({ where: { id: run.taskId }, data: { status: 'failed' } });
  await emitActivity({
    projectId: run.projectId, actor: 'system', type: 'project_failed',
    summary: `Collaboration failed: ${reason.slice(0, 200)}`,
    data: { projectRunId: run.id, phase: run.phase, reason, agentRunId: agentRunId ?? null },
    refId: run.id,
  });
  await notify({ type: 'agent_failure', title: 'Collaboration failed', body: reason.slice(0, 300), projectId: run.projectId });
}

function logNoTransition(run: LoadedProjectRun, why: string): void {
  console.warn('[collab] no transition', {
    projectRunId: run.id, status: run.status, phase: run.phase, iteration: run.iteration, why,
  });
}

export async function controlProjectRun(
  id: string,
  action: 'pause' | 'resume' | 'cancel' | 'retry',
): Promise<ProjectRun> {
  const run = await loadProjectRun(id);
  switch (action) {
    case 'pause': {
      await prisma.projectRun.updateMany({
        where: { id, status: { in: ['created', 'running'] } },
        data: { status: 'paused', lastTransitionAt: new Date() },
      });
      await emitActivity({ projectId: run.projectId, actor: 'user', type: 'status_change', summary: 'Collaboration paused by user', data: { projectRunId: id }, refId: id });
      break;
    }
    case 'resume': {
      await prisma.projectRun.updateMany({
        where: { id, status: 'paused' },
        data: { status: 'running', failureReason: null, lastTransitionAt: new Date() },
      });
      await emitActivity({ projectId: run.projectId, actor: 'user', type: 'status_change', summary: 'Collaboration resumed by user', data: { projectRunId: id }, refId: id });
      scheduleAdvance(id);
      break;
    }
    case 'cancel': {
      await prisma.projectRun.updateMany({
        where: { id, status: { in: ['created', 'running', 'paused', 'failed'] } },
        data: { status: 'cancelled', lastTransitionAt: new Date(), completedAt: new Date() },
      });
      if (run.taskId) await prisma.task.update({ where: { id: run.taskId }, data: { status: 'cancelled' } });
      await emitActivity({ projectId: run.projectId, actor: 'user', type: 'run_cancelled', summary: 'Collaboration cancelled by user', data: { projectRunId: id }, refId: id });
      break;
    }
    case 'retry': {
      // Clear failed/incomplete slots (completed steps are kept) and re-advance.
      const fresh = await loadProjectRun(id);
      if (!['failed', 'paused'].includes(fresh.status)) break;
      const steps = parseJson<Steps>(fresh.stepsJson, {});
      const keep: Steps = {};
      for (const [slot, runId] of Object.entries(steps)) {
        const child = await prisma.agentRun.findUnique({ where: { id: runId } });
        if (child?.status === 'completed') keep[slot] = runId;
      }
      await prisma.projectRun.update({
        where: { id },
        data: { status: 'running', failureReason: null, stepsJson: toJson(keep), lastTransitionAt: new Date() },
      });
      if (fresh.taskId) await prisma.task.update({ where: { id: fresh.taskId }, data: { status: 'in_progress' } });
      await emitActivity({ projectId: run.projectId, actor: 'user', type: 'status_change', summary: 'Collaboration retried by user (completed steps preserved)', data: { projectRunId: id }, refId: id });
      scheduleAdvance(id);
      break;
    }
  }
  return loadProjectRun(id);
}

/**
 * Boot recovery: resume collaborations that were mid-flight when the process
 * died. Steps whose AgentRun never completed are cleared (they will re-run);
 * completed steps are never duplicated.
 */
export async function recoverProjectRuns(): Promise<number> {
  const stale = await prisma.projectRun.findMany({ where: { status: { in: ['created', 'running'] } } });
  for (const run of stale) {
    const steps = parseJson<Steps>(run.stepsJson, {});
    const keep: Steps = {};
    for (const [slot, runId] of Object.entries(steps)) {
      const child = await prisma.agentRun.findUnique({ where: { id: runId } });
      if (child?.status === 'completed') {
        keep[slot] = runId;
      } else if (child && ['running', 'interrupted', 'queued'].includes(child.status)) {
        await prisma.agentRun.update({
          where: { id: child.id },
          data: { status: 'failed', error: 'Interrupted by application restart', finishedAt: new Date() },
        });
      }
    }
    await prisma.projectRun.update({ where: { id: run.id }, data: { stepsJson: toJson(keep), lastTransitionAt: new Date() } });
    await emitActivity({
      projectId: run.projectId, actor: 'system', type: 'status_change',
      summary: 'Collaboration recovered after restart — resuming from last completed step',
      data: { projectRunId: run.id, phase: run.phase, keptSteps: Object.keys(keep) },
      refId: run.id,
    });
    scheduleAdvance(run.id);
  }
  return stale.length;
}
