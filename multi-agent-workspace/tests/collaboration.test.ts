import { describe, expect, it } from 'vitest';
import { prisma } from '@/lib/db';
import {
  advanceProjectRun,
  controlProjectRun,
  recoverProjectRuns,
  startCollaboration,
} from '@/lib/orchestrator/collaboration';
import { AgentOutputSchema } from '@/lib/orchestrator/prompts';
import { addAgent, makeFixture, waitFor } from './helpers';

/**
 * Proves the failure mode is fixed: one user prompt produces a full
 * multi-step collaboration (analysis → cross review → synthesis →
 * verification → revision → completion) without any manual prompting.
 * These tests FAIL if the workflow stops after the first two responses.
 */

async function makeTwoAgentProject() {
  const f = await makeFixture({ role: 'Analyst' }); // agent A
  const b = await addAgent(f, `Bob-${Date.now()}`, 'Critic', ['complete_task']); // agent B
  return { ...f, agentB: b };
}

async function settled(projectRunId: string, timeoutMs = 20_000) {
  return waitFor(async () => {
    const r = await prisma.projectRun.findUnique({ where: { id: projectRunId } });
    return r && ['completed', 'failed', 'cancelled', 'paused'].includes(r.status) ? r : null;
  }, timeoutMs);
}

describe('collaboration orchestrator', () => {
  it('drives one prompt through the full two-agent workflow to completion', async () => {
    const f = await makeTwoAgentProject();
    const run = await startCollaboration(f.project.id, { prompt: 'Design a rollout plan for feature X' });
    const final = await settled(run.id);

    expect(final.status).toBe('completed');
    expect(final.phase).toBe('done');
    expect(final.iteration).toBe(1); // mock verifier requests exactly one revision
    expect(final.finalOutput).toContain('Revised result');

    // Multiple sequential model calls from ONE user prompt — the core fix.
    const steps = await prisma.agentRun.findMany({
      where: { projectRunId: run.id },
      orderBy: { createdAt: 'asc' },
    });
    const byType = (t: string) => steps.filter((s) => s.runType === t);
    expect(byType('initial')).toHaveLength(2); // both agents responded independently
    expect(byType('review')).toHaveLength(2); // each reviewed the other's work
    expect(byType('synthesis')).toHaveLength(1); // one agent synthesized
    expect(byType('verification')).toHaveLength(2); // rejected once, approved once
    expect(byType('revision')).toHaveLength(1); // author revised
    expect(steps.every((s) => s.status === 'completed')).toBe(true);

    // Agents actually received each other's work.
    const reviewCall = await prisma.modelCall.findFirst({
      where: { runId: byType('review')[0]!.id },
    });
    expect(reviewCall?.messagesJson).toContain('Response produced by');

    // Structured outputs persisted and valid.
    for (const s of steps) {
      expect(AgentOutputSchema.safeParse(JSON.parse(s.parsedOutputJson ?? 'null')).success).toBe(true);
    }

    // Every step is visible in the timeline; the run's model calls all recorded.
    const events = await prisma.auditEvent.findMany({ where: { projectId: f.project.id } });
    const types = new Set(events.map((e) => e.type));
    for (const t of ['project_run_started', 'task_created', 'agent_run_started', 'agent_run_completed', 'model_call', 'review_requested', 'revision_requested', 'project_finalizing', 'project_completed']) {
      expect(types, `missing timeline event ${t}`).toContain(t);
    }
    expect(await prisma.modelCall.count({ where: { run: { projectRunId: run.id } } })).toBe(8);

    // Final result persisted: umbrella task completed + artifact written.
    const task = await prisma.task.findUnique({ where: { id: final.taskId! } });
    expect(task?.status).toBe('completed');
    const artifact = await prisma.projectFile.findFirst({
      where: { projectId: f.project.id, path: { startsWith: 'results/collaboration-' } },
      include: { versions: true },
    });
    expect(artifact?.versions[0]?.content).toContain('Revised result');
  }, 30_000);

  it('does not duplicate steps when advance is called repeatedly (duplicate events)', async () => {
    const f = await makeTwoAgentProject();
    const run = await startCollaboration(f.project.id, { prompt: 'Duplicate-event resilience check' });
    // Hammer the transition function concurrently while it works.
    await Promise.all(Array.from({ length: 5 }, () => advanceProjectRun(run.id).catch(() => null)));
    const final = await settled(run.id);
    expect(final.status).toBe('completed');
    const counts = await prisma.agentRun.groupBy({
      by: ['runType'],
      where: { projectRunId: run.id },
      _count: true,
    });
    const map = Object.fromEntries(counts.map((c) => [c.runType, c._count]));
    expect(map).toEqual({ initial: 2, review: 2, synthesis: 1, verification: 2, revision: 1 });
  }, 30_000);

  it('stops revision loops at the iteration limit and finalizes with reservations', async () => {
    const f = await makeTwoAgentProject();
    const run = await startCollaboration(f.project.id, {
      prompt: '[test:always-revise] never-satisfiable request',
      maxIterations: 2,
      maxModelCalls: 30,
    });
    const final = await settled(run.id, 30_000);
    expect(final.status).toBe('completed');
    expect(final.iteration).toBe(2);
    expect(final.failureReason).toContain('Revision limit reached');
    expect(await prisma.agentRun.count({ where: { projectRunId: run.id, runType: 'revision' } })).toBe(2);
    expect(await prisma.agentRun.count({ where: { projectRunId: run.id, runType: 'verification' } })).toBe(3);
  }, 40_000);

  it('enforces the model-call budget with a visible failure', async () => {
    const f = await makeTwoAgentProject();
    const run = await startCollaboration(f.project.id, {
      prompt: 'Budget-capped collaboration',
      maxModelCalls: 3, // enough for phase 1 only
    });
    const final = await settled(run.id);
    expect(final.status).toBe('failed');
    expect(final.failureReason).toContain('Model call limit reached');
    const failEvent = await prisma.auditEvent.findFirst({
      where: { projectId: f.project.id, type: 'project_failed' },
    });
    expect(failEvent).not.toBeNull();
  }, 30_000);

  it('fails visibly on malformed JSON after one repair attempt, preserving the raw response, and supports retry', async () => {
    const f = await makeTwoAgentProject();
    const run = await startCollaboration(f.project.id, { prompt: '[test:malformed-json] break the parser' });
    const final = await settled(run.id);
    expect(final.status).toBe('failed');
    expect(final.failureReason).toContain('schema validation');

    const failed = await prisma.agentRun.findFirst({ where: { projectRunId: run.id, status: 'failed' } });
    expect(failed?.error).toContain('schema_validation_failed');
    expect(failed?.transcriptJson).toContain('not json'); // raw response stored
    // Two model calls: original + repair pass.
    expect(await prisma.modelCall.count({ where: { runId: failed!.id } })).toBe(2);

    // Retry is available (it will fail again with this marker, but must re-run rather than no-op).
    await controlProjectRun(run.id, 'retry');
    const after = await settled(run.id);
    expect(after.status).toBe('failed');
    const attempts = await prisma.agentRun.count({ where: { projectRunId: run.id, runType: 'initial', agentId: failed!.agentId } });
    expect(attempts).toBeGreaterThanOrEqual(2);
  }, 30_000);

  it('repairs repairable JSON in one pass and continues the workflow', async () => {
    const f = await makeTwoAgentProject();
    const run = await startCollaboration(f.project.id, {
      prompt: '[test:repairable-json] slightly broken output',
      maxModelCalls: 20, // repair passes add extra calls
    });
    const final = await settled(run.id);
    expect(final.status).toBe('completed');
    // Initial steps needed a repair call: > 1 model call on at least one initial run.
    const initials = await prisma.agentRun.findMany({ where: { projectRunId: run.id, runType: 'initial' } });
    const callCounts = await Promise.all(initials.map((s) => prisma.modelCall.count({ where: { runId: s.id } })));
    expect(Math.max(...callCounts)).toBe(2);
  }, 30_000);

  it('stitches truncated responses together via continuation calls', async () => {
    const f = await makeTwoAgentProject();
    const run = await startCollaboration(f.project.id, {
      prompt: '[test:truncated-json] very long output',
      maxModelCalls: 20, // continuations add extra calls
    });
    const final = await settled(run.id);
    expect(final.status).toBe('completed');
    // Each initial step needed one continuation call (2 model calls per step).
    const initials = await prisma.agentRun.findMany({ where: { projectRunId: run.id, runType: 'initial' } });
    for (const s of initials) {
      expect(await prisma.modelCall.count({ where: { runId: s.id } })).toBe(2);
      const parsed = JSON.parse(s.parsedOutputJson ?? 'null') as { workProduct: string } | null;
      expect(parsed?.workProduct).toContain('Long proposal'); // stitched JSON parsed cleanly
    }
    const continuation = await prisma.modelCall.findFirst({
      where: { run: { projectRunId: run.id } },
      orderBy: { createdAt: 'asc' },
      skip: 1,
    });
    expect(continuation?.contextJson).toContain('continuation');
  }, 30_000);

  it('supports pause and resume mid-workflow', async () => {
    const f = await makeTwoAgentProject();
    const run = await startCollaboration(f.project.id, { prompt: 'Pausable collaboration' });
    await controlProjectRun(run.id, 'pause');
    const paused = await settled(run.id);
    if (paused.status === 'paused') {
      await controlProjectRun(run.id, 'resume');
      const final = await settled(run.id);
      expect(final.status).toBe('completed');
    } else {
      // The run finished before the pause landed — acceptable, it must be completed.
      expect(paused.status).toBe('completed');
    }
  }, 30_000);

  it('recovers after a simulated worker restart without duplicating completed steps', async () => {
    const f = await makeTwoAgentProject();
    // Construct the exact persisted state of a crash mid-cross-review: both
    // initial steps completed, one review step stuck "running" when the
    // process died, and no advance loop currently active.
    const output = (name: string) =>
      JSON.stringify({
        summary: `${name} initial proposal.`,
        workProduct: `Proposal from ${name}: scope, implementation, verification.`,
        status: 'needs_review',
        nextAction: 'send_to_reviewer',
        questions: [], issues: [], acceptanceCriteriaResults: [],
      });
    const task = await prisma.task.create({
      data: { projectId: f.project.id, title: 'Collaborate: restart recovery', description: 'Restart recovery check', status: 'in_progress', createdBy: 'user' },
    });
    const mkStep = (agentId: string, runType: string, status: string, parsed: string | null, projectRunId: string) =>
      prisma.agentRun.create({
        data: {
          projectId: f.project.id, agentId, taskId: task.id, projectRunId, runType,
          objective: `[${runType}] Restart recovery check`, status,
          parsedOutputJson: parsed, resultSummary: parsed ? 'done' : '',
          startedAt: new Date(), finishedAt: parsed ? new Date() : null,
        },
      });
    const projectRun = await prisma.projectRun.create({
      data: {
        projectId: f.project.id, taskId: task.id, prompt: 'Restart recovery check',
        status: 'running', phase: 'cross_review',
        agentAId: f.agent.id, agentBId: f.agentB.id, synthesizerId: f.agent.id,
        startedAt: new Date(),
      },
    });
    const initialA = await mkStep(f.agent.id, 'initial', 'completed', output('A'), projectRun.id);
    const initialB = await mkStep(f.agentB.id, 'initial', 'completed', output('B'), projectRun.id);
    const stuckReview = await mkStep(f.agent.id, 'review', 'running', null, projectRun.id);
    await prisma.projectRun.update({
      where: { id: projectRun.id },
      data: {
        stepsJson: JSON.stringify({ 'initial:A': initialA.id, 'initial:B': initialB.id, 'review:A': stuckReview.id }),
      },
    });

    // "Restart": boot recovery fails the stuck step, keeps completed ones, resumes.
    await recoverProjectRuns();
    const final = await settled(projectRun.id);
    expect(final.status).toBe('completed');

    // Initial steps were reused, not re-executed.
    const initialAfter = await prisma.agentRun.findMany({ where: { projectRunId: projectRun.id, runType: 'initial' } });
    expect(initialAfter.map((s) => s.id).sort()).toEqual([initialA.id, initialB.id].sort());
    // The stuck review was failed and re-run once; nothing else duplicated.
    const reviewsAfter = await prisma.agentRun.findMany({ where: { projectRunId: projectRun.id, runType: 'review' } });
    expect(reviewsAfter.find((s) => s.id === stuckReview.id)?.status).toBe('failed');
    expect(reviewsAfter.filter((s) => s.status === 'completed')).toHaveLength(2);
    expect(await prisma.agentRun.count({ where: { projectRunId: projectRun.id, runType: 'synthesis' } })).toBe(1);
    const task2 = await prisma.task.findUnique({ where: { id: task.id } });
    expect(task2?.status).toBe('completed');
  }, 40_000);

  it('requires at least two agents', async () => {
    const f = await makeFixture();
    await expect(startCollaboration(f.project.id, { prompt: 'solo?' })).rejects.toThrow(/at least 2 agents/);
  });

  it('is idempotent on the start call', async () => {
    const f = await makeTwoAgentProject();
    const key = `collab-${Date.now()}`;
    const a = await startCollaboration(f.project.id, { prompt: 'same run', idempotencyKey: key });
    const b = await startCollaboration(f.project.id, { prompt: 'same run again', idempotencyKey: key });
    expect(b.id).toBe(a.id);
    await settled(a.id);
  }, 30_000);
});
