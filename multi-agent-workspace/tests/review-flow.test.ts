import { describe, expect, it } from 'vitest';
import { prisma } from '@/lib/db';
import { runDemo } from '@/lib/orchestrator/demo';
import { addAgent, BUILDER_TOOLS, makeFixture } from './helpers';

/**
 * Full integration test of the collaborative flow: plan → architecture →
 * implement → review (changes requested) → fix → re-review (approved) →
 * QA → completion report. Uses the deterministic mock provider.
 */
describe('multi-agent review workflow (demonstration pipeline)', () => {
  it('runs the whole collaborative build end to end', async () => {
    const f = await makeFixture({ role: 'Project Manager', tools: BUILDER_TOOLS });
    await addAgent(f, 'Arch', 'Software Architect', BUILDER_TOOLS);
    await addAgent(f, 'Dev', 'Developer', BUILDER_TOOLS);
    await addAgent(f, 'Rev', 'Code Reviewer', BUILDER_TOOLS);
    await addAgent(f, 'Q', 'QA Tester', BUILDER_TOOLS);

    const { steps } = await runDemo(f.project.id);
    expect(steps).toHaveLength(8);

    // Every run completed.
    const runs = await prisma.agentRun.findMany({ where: { projectId: f.project.id } });
    expect(runs).toHaveLength(8);
    expect(runs.every((r) => r.status === 'completed')).toBe(true);

    // The PM delegated three tasks; all tasks ended completed.
    const tasks = await prisma.task.findMany({ where: { projectId: f.project.id } });
    expect(tasks.length).toBe(5); // plan + 3 delegated + completion report
    expect(tasks.every((t) => t.status === 'completed')).toBe(true);

    // The review cycle produced two versions of the implementation.
    const impl = await prisma.projectFile.findUnique({
      where: { projectId_path: { projectId: f.project.id, path: 'src/calculator.js' } },
      include: { versions: { orderBy: { version: 'asc' } } },
    });
    expect(impl?.latestVersion).toBe(2);
    expect(impl?.versions[0]?.content).not.toContain('RangeError');
    expect(impl?.versions[1]?.content).toContain('RangeError');

    // Review messages: one "changes requested", one approval.
    const reviews = await prisma.message.findMany({
      where: { projectId: f.project.id, type: 'review_result' },
      orderBy: { createdAt: 'asc' },
    });
    expect(reviews).toHaveLength(2);
    expect(reviews[0]!.content).toContain('CHANGES REQUESTED');
    expect(reviews[1]!.content).toContain('APPROVED');

    // Artifacts, decision log and audit history all populated.
    const paths = (await prisma.projectFile.findMany({ where: { projectId: f.project.id } })).map((x) => x.path);
    expect(paths).toEqual(
      expect.arrayContaining(['docs/ARCHITECTURE.md', 'src/calculator.js', 'reports/QA_REPORT.md', 'reports/COMPLETION_REPORT.md']),
    );
    expect(await prisma.decision.count({ where: { projectId: f.project.id } })).toBeGreaterThan(0);
    expect(await prisma.auditEvent.count({ where: { projectId: f.project.id } })).toBeGreaterThan(20);
    expect(await prisma.modelCall.count({ where: { projectId: f.project.id } })).toBeGreaterThan(10);

    // Project marked completed.
    const project = await prisma.project.findUnique({ where: { id: f.project.id } });
    expect(project?.status).toBe('completed');
  }, 60_000);

  it('is idempotent: rerunning the demo does not duplicate runs', async () => {
    const f = await makeFixture({ role: 'Project Manager', tools: BUILDER_TOOLS });
    await addAgent(f, 'Arch2', 'Software Architect', BUILDER_TOOLS);
    await addAgent(f, 'Dev2', 'Developer', BUILDER_TOOLS);
    await addAgent(f, 'Rev2', 'Code Reviewer', BUILDER_TOOLS);
    await addAgent(f, 'Q2', 'QA Tester', BUILDER_TOOLS);

    await runDemo(f.project.id);
    const runsAfterFirst = await prisma.agentRun.count({ where: { projectId: f.project.id } });
    await runDemo(f.project.id); // idempotency keys reuse the same runs
    const runsAfterSecond = await prisma.agentRun.count({ where: { projectId: f.project.id } });
    expect(runsAfterSecond).toBe(runsAfterFirst);
  }, 60_000);
});
