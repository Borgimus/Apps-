import { describe, expect, it } from 'vitest';
import { prisma } from '@/lib/db';
import {
  cancelRun,
  createRun,
  pauseRun,
  processRun,
  resolveApproval,
  resumeRun,
  startRun,
  startRunInBackground,
} from '@/lib/orchestrator/engine';
import { makeFixture, waitFor } from './helpers';

describe('orchestration engine', () => {
  it('completes a simple run and records model calls, usage and history', async () => {
    const f = await makeFixture();
    const run = await startRun({
      agentId: f.agent.id,
      projectId: f.project.id,
      objective: '[test:noop] simple task',
    });
    expect(run.status).toBe('completed');
    expect(run.resultSummary).toContain('No-op');

    const calls = await prisma.modelCall.findMany({ where: { runId: run.id } });
    expect(calls.length).toBeGreaterThan(0);
    expect(calls[0]!.systemPrompt).toContain('ROLE: Generalist');
    expect(calls[0]!.systemPrompt).toContain(f.project.name);

    const usage = await prisma.usageRecord.count({ where: { runId: run.id } });
    expect(usage).toBe(calls.length);

    const events = await prisma.auditEvent.count({ where: { projectId: f.project.id } });
    expect(events).toBeGreaterThan(2);
  });

  it('executes permitted file writes and versions the file', async () => {
    const f = await makeFixture();
    const run = await startRun({
      agentId: f.agent.id,
      projectId: f.project.id,
      objective: '[test:write] write a note',
    });
    expect(run.status).toBe('completed');
    const file = await prisma.projectFile.findUnique({
      where: { projectId_path: { projectId: f.project.id, path: 'notes.txt' } },
      include: { versions: true },
    });
    expect(file?.latestVersion).toBe(1);
    expect(file?.versions[0]?.content).toBe('hello from mock');
    expect(file?.versions[0]?.authorAgentId).toBe(f.agent.id);
  });

  it('denies tools outside the agent permission set and stops at max iterations', async () => {
    const f = await makeFixture({ tools: ['read_file', 'complete_task'] }); // no write_file
    const run = await startRun({
      agentId: f.agent.id,
      projectId: f.project.id,
      objective: '[test:forbidden-tool] try to write',
      maxIterations: 3,
    });
    expect(run.status).toBe('failed');
    expect(run.error).toContain('Maximum iterations');

    const denied = await prisma.toolCall.findMany({ where: { runId: run.id, status: 'denied' } });
    expect(denied.length).toBeGreaterThan(0);
    expect(denied[0]!.toolName).toBe('write_file');
    // The denial must not have produced a file.
    const file = await prisma.projectFile.findFirst({ where: { projectId: f.project.id } });
    expect(file).toBeNull();
  });

  it('finishes gracefully when the model stops calling tools', async () => {
    const f = await makeFixture();
    const run = await startRun({
      agentId: f.agent.id,
      projectId: f.project.id,
      objective: '[test:loop] think forever',
      maxIterations: 10,
    });
    // Two consecutive text-only turns are treated as an implicit completion.
    expect(run.status).toBe('completed');
    expect(run.iterations).toBeLessThanOrEqual(4);
  });

  it('pauses a gated tool call behind an approval and resumes on approve', async () => {
    const f = await makeFixture({ permissions: { fileWrite: true, fileWriteRequiresApproval: true } });
    const run = await startRun({
      agentId: f.agent.id,
      projectId: f.project.id,
      objective: '[test:write] gated write',
    });
    expect(run.status).toBe('awaiting_approval');

    const approval = await prisma.approvalRequest.findFirst({
      where: { runId: run.id, status: 'pending' },
    });
    expect(approval?.action).toBe('tool:write_file');
    // Nothing executed yet.
    expect(await prisma.projectFile.findFirst({ where: { projectId: f.project.id } })).toBeNull();

    await resolveApproval(approval!.id, 'approved');
    const finished = await waitFor(async () => {
      const r = await prisma.agentRun.findUnique({ where: { id: run.id } });
      return r?.status === 'completed' ? r : null;
    });
    expect(finished.status).toBe('completed');
    const file = await prisma.projectFile.findFirst({ where: { projectId: f.project.id } });
    expect(file?.path).toBe('notes.txt');
  });

  it('feeds a rejection back to the agent instead of executing the tool', async () => {
    const f = await makeFixture({ permissions: { fileWrite: true, fileWriteRequiresApproval: true } });
    const run = await startRun({
      agentId: f.agent.id,
      projectId: f.project.id,
      objective: '[test:write] gated write to reject',
    });
    const approval = await prisma.approvalRequest.findFirstOrThrow({ where: { runId: run.id, status: 'pending' } });
    await resolveApproval(approval.id, 'rejected', 'not now');

    const finished = await waitFor(async () => {
      const r = await prisma.agentRun.findUnique({ where: { id: run.id } });
      return r && ['completed', 'failed'].includes(r.status) ? r : null;
    });
    expect(finished.status).toBe('completed'); // the agent adapts and finishes
    expect(await prisma.projectFile.findFirst({ where: { projectId: f.project.id } })).toBeNull();
    const rejected = await prisma.toolCall.findFirst({ where: { runId: run.id, status: 'rejected' } });
    expect(rejected?.toolName).toBe('write_file');
  });

  it('supports pause and resume', async () => {
    const f = await makeFixture();
    const run = await createRun({
      agentId: f.agent.id,
      projectId: f.project.id,
      objective: '[test:noop] pausable',
    });
    const paused = await pauseRun(run.id);
    expect(paused.status).toBe('paused');
    // Processing a paused run is a no-op.
    expect((await processRun(run.id)).status).toBe('paused');

    await resumeRun(run.id);
    const finished = await waitFor(async () => {
      const r = await prisma.agentRun.findUnique({ where: { id: run.id } });
      return r?.status === 'completed' ? r : null;
    });
    expect(finished.status).toBe('completed');
  });

  it('cancels a run mid-flight without executing its pending work', async () => {
    const f = await makeFixture();
    const run = await startRunInBackground({
      agentId: f.agent.id,
      projectId: f.project.id,
      objective: '[test:slow] slow task',
    });
    await new Promise((r) => setTimeout(r, 100)); // provider is mid-"thought"
    await cancelRun(run.id);
    const settled = await waitFor(async () => {
      const r = await prisma.agentRun.findUnique({ where: { id: run.id } });
      return r?.status === 'cancelled' ? r : null;
    });
    expect(settled.status).toBe('cancelled');
    await new Promise((r) => setTimeout(r, 400)); // let the in-flight iteration land
    const r = await prisma.agentRun.findUnique({ where: { id: run.id } });
    expect(r?.status).toBe('cancelled');
    expect(r?.resultSummary).toBe(''); // never completed
  });

  it('prevents duplicate execution via idempotency keys', async () => {
    const f = await makeFixture();
    const key = `dup-${Date.now()}`;
    const a = await createRun({ agentId: f.agent.id, projectId: f.project.id, objective: '[test:noop] once', idempotencyKey: key });
    const b = await createRun({ agentId: f.agent.id, projectId: f.project.id, objective: '[test:noop] twice', idempotencyKey: key });
    expect(b.id).toBe(a.id);
    expect(await prisma.agentRun.count({ where: { idempotencyKey: key } })).toBe(1);
  });

  it('stops and requests approval when the per-run budget is exhausted', async () => {
    const f = await makeFixture({ maxCostPerRunUsd: 0.000001, pricingPerMTok: 1000 });
    const run = await startRun({
      agentId: f.agent.id,
      projectId: f.project.id,
      objective: '[test:loop] expensive thinking',
      maxIterations: 10,
    });
    expect(run.status).toBe('awaiting_approval');
    const approval = await prisma.approvalRequest.findFirst({ where: { runId: run.id, action: 'budget_increase' } });
    expect(approval).not.toBeNull();
  });

  it('records provider failures as failed runs with the error preserved', async () => {
    const f = await makeFixture();
    const run = await startRun({
      agentId: f.agent.id,
      projectId: f.project.id,
      objective: '[test:error] provider blows up',
    });
    expect(run.status).toBe('failed');
    expect(run.error).toContain('Simulated provider failure');
    const errCall = await prisma.modelCall.findFirst({ where: { runId: run.id, status: 'error' } });
    expect(errCall).not.toBeNull();
  }, 20_000);
});
