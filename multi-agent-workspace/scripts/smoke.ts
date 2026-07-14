import { prisma } from '../src/lib/db';
import { runDemo } from '../src/lib/orchestrator/demo';

async function main() {
  const project = await prisma.project.findFirstOrThrow({
    where: { name: 'Collaborative Software Build' },
  });
  const { steps } = await runDemo(project.id);
  console.log('Steps:', steps.length);

  const [tasks, runs, calls, tools, files, msgs, decisions, events, usage] = await Promise.all([
    prisma.task.findMany({ where: { projectId: project.id } }),
    prisma.agentRun.findMany({ where: { projectId: project.id } }),
    prisma.modelCall.count({ where: { projectId: project.id } }),
    prisma.toolCall.count({ where: { projectId: project.id } }),
    prisma.projectFile.findMany({ where: { projectId: project.id } }),
    prisma.message.count({ where: { projectId: project.id } }),
    prisma.decision.count({ where: { projectId: project.id } }),
    prisma.auditEvent.count({ where: { projectId: project.id } }),
    prisma.usageRecord.aggregate({ where: { projectId: project.id }, _sum: { inputTokens: true, outputTokens: true } }),
  ]);
  console.log('Tasks:', tasks.map((t) => `${t.title} [${t.status}]`));
  console.log('Runs:', runs.map((r) => `${r.status} iter=${r.iterations}`));
  console.log('ModelCalls:', calls, 'ToolCalls:', tools, 'Messages:', msgs, 'Decisions:', decisions, 'Events:', events);
  console.log('Files:', files.map((f) => `${f.path} v${f.latestVersion}`));
  console.log('Tokens:', usage._sum);
}

main()
  .catch((e) => {
    console.error(e);
    process.exit(1);
  })
  .finally(() => prisma.$disconnect());
