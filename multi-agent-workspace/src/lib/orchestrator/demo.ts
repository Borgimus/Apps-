import { prisma } from '../db';
import { emitActivity } from '../events';
import { startRun } from './engine';

/**
 * Seeded demonstration: "Collaborative Software Build".
 *
 * Drives the full multi-agent flow through the real orchestration engine
 * (using whatever model each agent is configured with — the seed uses the
 * mock provider so it runs without API keys):
 *
 *   1. Project Manager decomposes the feature request into tasks
 *   2. Architect proposes an implementation
 *   3. Developer implements
 *   4. Reviewer requests changes
 *   5. Developer addresses the feedback
 *   6. Reviewer approves
 *   7. QA verifies
 *   8. Project Manager writes the completion report
 */
export async function runDemo(projectId: string): Promise<{ steps: string[] }> {
  const project = await prisma.project.findUnique({
    where: { id: projectId },
    include: { agents: { include: { agent: true } } },
  });
  if (!project) throw new Error('Project not found');

  const byRole = (needle: string) => {
    const found = project.agents.find((a) => a.agent.role.toLowerCase().includes(needle));
    if (!found) throw new Error(`Demo requires an agent with role containing "${needle}"`);
    return found.agent;
  };
  const pm = byRole('project manager');
  const architect = byRole('architect');
  const developer = byRole('developer');
  const reviewer = byRole('reviewer');
  const qa = byRole('qa');

  const steps: string[] = [];
  const step = async (label: string) => {
    steps.push(label);
    await emitActivity({
      projectId,
      actor: 'system',
      type: 'demo_step',
      summary: `Demo: ${label}`,
      data: { step: steps.length },
    });
  };

  const expectCompleted = (label: string, run: { status: string; error: string | null }) => {
    if (run.status !== 'completed') {
      throw new Error(`Demo step "${label}" ended with status ${run.status}${run.error ? `: ${run.error}` : ''}`);
    }
  };

  // 1. PM decomposes the feature request.
  await step('Project Manager decomposes the feature request');
  const planTask = await prisma.task.create({
    data: {
      projectId,
      title: 'Plan the calculator feature',
      description:
        'Break the feature request ("build a calculator module with add/subtract/multiply/divide") into delegated tasks.',
      status: 'ready',
      priority: 'critical',
      ownerAgentId: pm.id,
      createdBy: 'user',
    },
  });
  const pmRun = await startRun({
    agentId: pm.id,
    projectId,
    taskId: planTask.id,
    objective:
      'Decompose the calculator feature request into tasks and delegate them to the right agents by role (Software Architect, Developer with Code Reviewer as reviewer, QA Tester).',
    idempotencyKey: `demo:${projectId}:pm-plan`,
  });
  expectCompleted('PM planning', pmRun);

  const findTask = async (needle: string) => {
    const t = await prisma.task.findFirst({
      where: { projectId, title: { contains: needle } },
      orderBy: { createdAt: 'desc' },
    });
    if (!t) throw new Error(`Demo: expected the PM to create a task matching "${needle}"`);
    return t;
  };
  const archTask = await findTask('architecture');
  const devTask = await findTask('Implement');
  const qaTask = await findTask('QA');

  // 2. Architect proposes the implementation.
  await step('Architect proposes the implementation');
  expectCompleted(
    'Architecture',
    await startRun({
      agentId: architect.id,
      projectId,
      taskId: archTask.id,
      objective: archTask.description || 'Propose the architecture for the calculator module.',
      idempotencyKey: `demo:${projectId}:architecture`,
    }),
  );

  // 3. Developer implements.
  await step('Developer implements the module');
  const devRun = await startRun({
    agentId: developer.id,
    projectId,
    taskId: devTask.id,
    objective: devTask.description || 'Implement the calculator module per the architecture.',
    idempotencyKey: `demo:${projectId}:implement`,
  });
  expectCompleted('Implementation', devRun);

  // 4. Reviewer reviews (finds the divide-by-zero defect).
  await step('Code Reviewer reviews the implementation');
  expectCompleted(
    'Review round 1',
    await startRun({
      agentId: reviewer.id,
      projectId,
      taskId: devTask.id,
      objective: 'Review src/calculator.js against the architecture and acceptance criteria. Report your verdict.',
      idempotencyKey: `demo:${projectId}:review-1`,
    }),
  );

  // 5. Developer addresses review feedback.
  await step('Developer addresses review feedback');
  expectCompleted(
    'Fixup',
    await startRun({
      agentId: developer.id,
      projectId,
      taskId: devTask.id,
      objective: 'Address the review feedback on src/calculator.js (add the divide-by-zero guard) — review feedback fixup.',
      idempotencyKey: `demo:${projectId}:implement-fix`,
    }),
  );

  // 6. Reviewer approves.
  await step('Code Reviewer approves the fix');
  expectCompleted(
    'Review round 2',
    await startRun({
      agentId: reviewer.id,
      projectId,
      taskId: devTask.id,
      objective: 'Re-review src/calculator.js after the fix. Report your verdict.',
      idempotencyKey: `demo:${projectId}:review-2`,
    }),
  );

  // 7. QA verifies.
  await step('QA Tester verifies the result');
  expectCompleted(
    'QA',
    await startRun({
      agentId: qa.id,
      projectId,
      taskId: qaTask.id,
      objective: qaTask.description || 'Verify the calculator implementation and write a QA report.',
      idempotencyKey: `demo:${projectId}:qa`,
    }),
  );

  // 8. PM writes the completion report.
  await step('Project Manager writes the completion report');
  const reportTask = await prisma.task.create({
    data: {
      projectId,
      title: 'Prepare final completion report',
      description: 'Summarize the work: architecture, implementation, review cycle, QA verification.',
      status: 'ready',
      priority: 'high',
      ownerAgentId: pm.id,
      createdBy: 'user',
    },
  });
  expectCompleted(
    'Completion report',
    await startRun({
      agentId: pm.id,
      projectId,
      taskId: reportTask.id,
      objective: 'Write the final completion report for the calculator feature.',
      idempotencyKey: `demo:${projectId}:pm-report`,
    }),
  );

  await prisma.project.update({ where: { id: projectId }, data: { status: 'completed' } });
  await emitActivity({
    projectId,
    actor: 'system',
    type: 'project_completed',
    summary: 'Demonstration complete: all agents collaborated to deliver the calculator feature.',
    data: { steps },
  });
  return { steps };
}
