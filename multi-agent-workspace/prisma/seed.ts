import { PrismaClient } from '@prisma/client';

const prisma = new PrismaClient();

/**
 * Idempotent seed: workspace, model configurations, agent templates, and the
 * "Collaborative Software Build" demonstration project (5 agents on the mock
 * provider so it runs with zero API keys).
 */

const CORE_TOOLS = ['list_files', 'read_file', 'send_message', 'record_decision', 'request_approval', 'complete_task'];
const BUILDER_TOOLS = [...CORE_TOOLS, 'write_file', 'create_task', 'update_task', 'request_review'];

const TEMPLATES: Array<{ name: string; role: string; description: string; systemPrompt: string; tools: string[] }> = [
  {
    name: 'Project Manager',
    role: 'Project Manager',
    description: 'Decomposes objectives into tasks, delegates by role, tracks progress, integrates results.',
    systemPrompt:
      'You are a pragmatic project manager. Decompose objectives into small, verifiable tasks with clear acceptance criteria, delegate each to the best-suited agent by role, monitor progress, and produce integration and completion reports. Never do specialist work yourself — delegate it.',
    tools: BUILDER_TOOLS,
  },
  {
    name: 'Lead Developer',
    role: 'Developer',
    description: 'Implements features with clean, tested code.',
    systemPrompt:
      'You are a senior software developer. Read the relevant architecture and existing files before writing code. Implement exactly what the task requires with clean, minimal, well-structured code. Request review when your implementation is ready.',
    tools: BUILDER_TOOLS,
  },
  {
    name: 'Software Architect',
    role: 'Software Architect',
    description: 'Designs implementations and records key technical decisions.',
    systemPrompt:
      'You are a software architect. Produce clear, minimal designs: API surface, error handling, data flow, and test strategy. Write your proposals to docs/ and record key choices with record_decision.',
    tools: BUILDER_TOOLS,
  },
  {
    name: 'Researcher',
    role: 'Researcher',
    description: 'Gathers evidence and summarizes findings with sources and assumptions.',
    systemPrompt:
      'You are a rigorous researcher. Separate facts from assumptions, cite where each finding came from, and write concise findings documents. Flag uncertainty honestly.',
    tools: BUILDER_TOOLS,
  },
  {
    name: 'Code Reviewer',
    role: 'Code Reviewer',
    description: 'Reviews implementations for correctness, clarity, and adherence to the design.',
    systemPrompt:
      'You are a meticulous code reviewer. Read the code under review and check it against the architecture and acceptance criteria. Report a clear verdict via send_message (type review_result): either "APPROVED" or "CHANGES REQUESTED" with specific, actionable findings. Include the phrase "changes requested" in your completion summary when you request changes.',
    tools: [...CORE_TOOLS, 'update_task'],
  },
  {
    name: 'Security Reviewer',
    role: 'Security Reviewer',
    description: 'Audits changes for vulnerabilities and unsafe handling of data and secrets.',
    systemPrompt:
      'You are a security reviewer. Examine code and plans for injection risks, secret exposure, unsafe input handling, and privilege escalation. Report findings by severity with concrete remediation steps.',
    tools: [...CORE_TOOLS, 'update_task'],
  },
  {
    name: 'QA Tester',
    role: 'QA Tester',
    description: 'Verifies outputs against acceptance criteria and writes test reports.',
    systemPrompt:
      'You are a thorough QA tester. Verify deliverables against every acceptance criterion, probe edge cases, and write a pass/fail report to reports/. Never mark work as passing without checking it.',
    tools: BUILDER_TOOLS,
  },
  {
    name: 'Product Designer',
    role: 'Product Designer',
    description: 'Designs user experiences and interface specifications.',
    systemPrompt:
      'You are a product designer. Produce clear UX specifications: user flows, states (empty, loading, error), and accessibility notes. Write specs to docs/ and flag open questions.',
    tools: BUILDER_TOOLS,
  },
  {
    name: 'Documentation Writer',
    role: 'Documentation Writer',
    description: 'Writes accurate, concise documentation from project artifacts.',
    systemPrompt:
      'You are a technical writer. Read the actual code and decisions before documenting — never invent behavior. Write concise, structured docs to docs/.',
    tools: BUILDER_TOOLS,
  },
  {
    name: "Devil's Advocate",
    role: "Devil's Advocate",
    description: 'Challenges proposals, surfaces risks, and argues the strongest counter-position.',
    systemPrompt:
      "You are the devil's advocate. For any proposal, find the strongest objections: hidden assumptions, failure modes, cheaper alternatives. Raise them via send_message (type objection) and record unresolved disagreements as decision proposals. Be adversarial to ideas, never to people.",
    tools: CORE_TOOLS,
  },
  {
    name: 'Data Analyst',
    role: 'Data Analyst',
    description: 'Analyzes data and produces evidence-backed summaries.',
    systemPrompt:
      'You are a data analyst. Work from the actual data in project files, show your method, quantify uncertainty, and write findings to reports/.',
    tools: BUILDER_TOOLS,
  },
];

async function main() {
  // --- Workspace (single-user mode) ---
  let workspace = await prisma.workspace.findFirst();
  if (!workspace) {
    workspace = await prisma.workspace.create({
      data: {
        name: 'My Workspace',
        instructions:
          'Be concise and factual. Prefer small verifiable steps. Every claim about project state must be backed by files, tasks, or decisions on record.',
        dailyBudgetUsd: 25,
      },
    });
  }

  // --- Model configurations (separate from agents; agents can be re-pointed) ---
  const modelConfigs = [
    {
      name: 'Mock Model (free, deterministic)',
      provider: 'mock',
      modelId: 'mock-1',
      temperature: 0,
      maxTokens: 4096,
      contextWindow: 100000,
      inputPricePerMTok: 0,
      outputPricePerMTok: 0,
    },
    {
      name: 'Claude Fable 5',
      provider: 'anthropic',
      modelId: 'claude-fable-5',
      apiKeyEnvVar: 'ANTHROPIC_API_KEY',
      temperature: 0.3,
      maxTokens: 8192,
      contextWindow: 200000,
      inputPricePerMTok: 5,
      outputPricePerMTok: 25,
    },
    {
      name: 'Claude Sonnet 5',
      provider: 'anthropic',
      modelId: 'claude-sonnet-5',
      apiKeyEnvVar: 'ANTHROPIC_API_KEY',
      temperature: 0.3,
      maxTokens: 8192,
      contextWindow: 200000,
      inputPricePerMTok: 3,
      outputPricePerMTok: 15,
    },
    {
      // Placeholder identifier — repoint modelId/baseUrl when a real API is available.
      name: 'Sol 5.6 (placeholder)',
      provider: 'openai-compatible',
      modelId: 'sol-5.6',
      baseUrl: process.env.SOL_BASE_URL ?? null,
      apiKeyEnvVar: 'SOL_API_KEY',
      temperature: 0.3,
      maxTokens: 8192,
      contextWindow: 200000,
      inputPricePerMTok: 0,
      outputPricePerMTok: 0,
    },
    {
      name: 'OpenAI GPT (openai-compatible)',
      provider: 'openai-compatible',
      modelId: 'gpt-4o',
      apiKeyEnvVar: 'OPENAI_API_KEY',
      temperature: 0.3,
      maxTokens: 8192,
      contextWindow: 128000,
      inputPricePerMTok: 2.5,
      outputPricePerMTok: 10,
    },
    {
      name: 'Ollama local model',
      provider: 'openai-compatible',
      modelId: 'llama3.1',
      baseUrl: process.env.OLLAMA_BASE_URL ?? 'http://localhost:11434/v1',
      temperature: 0.3,
      maxTokens: 4096,
      contextWindow: 32000,
      inputPricePerMTok: 0,
      outputPricePerMTok: 0,
    },
  ];
  for (const cfg of modelConfigs) {
    await prisma.modelConfig.upsert({ where: { name: cfg.name }, update: cfg, create: cfg });
  }
  const mock = await prisma.modelConfig.findUniqueOrThrow({ where: { name: 'Mock Model (free, deterministic)' } });

  // --- Agent templates ---
  for (const t of TEMPLATES) {
    const data = {
      name: t.name,
      role: t.role,
      description: t.description,
      systemPrompt: t.systemPrompt,
      toolsJson: JSON.stringify(t.tools),
    };
    await prisma.agentTemplate.upsert({ where: { name: t.name }, update: data, create: data });
  }

  // --- Demonstration project ---
  const existingDemo = await prisma.project.findFirst({ where: { name: 'Collaborative Software Build' } });
  if (!existingDemo) {
    const project = await prisma.project.create({
      data: {
        workspaceId: workspace.id,
        name: 'Collaborative Software Build',
        objective:
          'Deliver a small calculator module (add, subtract, multiply, divide) through a full multi-agent workflow: planning, architecture, implementation, code review, QA, and a completion report.',
        instructions:
          'Follow the pipeline: Project Manager plans → Architect designs → Developer implements → Reviewer reviews → Developer fixes → QA verifies → PM reports. Keep artifacts in docs/, src/ and reports/.',
        orchestrationMode: 'pipeline',
        budgetUsd: 5,
      },
    });

    const demoAgents: Array<{ template: string; name: string }> = [
      { template: 'Project Manager', name: 'Morgan (PM)' },
      { template: 'Software Architect', name: 'Alex (Architect)' },
      { template: 'Lead Developer', name: 'Devin (Developer)' },
      { template: 'Code Reviewer', name: 'Rae (Reviewer)' },
      { template: 'QA Tester', name: 'Quinn (QA)' },
    ];
    for (const spec of demoAgents) {
      const template = await prisma.agentTemplate.findUniqueOrThrow({ where: { name: spec.template } });
      const agent = await prisma.agent.create({
        data: {
          workspaceId: workspace.id,
          name: spec.name,
          role: template.role,
          systemPrompt: template.systemPrompt,
          modelConfigId: mock.id,
          toolsJson: template.toolsJson,
          permissionsJson: JSON.stringify({ fileWrite: true, fileWriteRequiresApproval: false, network: false }),
          maxCostPerRunUsd: 1,
        },
      });
      await prisma.projectAgent.create({ data: { projectId: project.id, agentId: agent.id } });
    }

    await prisma.projectMemory.create({
      data: {
        projectId: project.id,
        key: 'feature-request',
        content:
          'Build a calculator module exposing add, subtract, multiply and divide. Division by zero must raise a clear error. Deliverables: architecture doc, implementation, review sign-off, QA report.',
        pinned: true,
      },
    });
    await prisma.auditEvent.create({
      data: {
        projectId: project.id,
        actor: 'system',
        type: 'project_created',
        summary: 'Demonstration project seeded. Open the project and press "Run demo" to watch the agents collaborate.',
        dataJson: '{}',
      },
    });
    console.log(`Seeded demo project: ${project.id}`);
  } else {
    console.log('Demo project already present — skipping.');
  }

  console.log('Seed complete.');
}

main()
  .catch((e) => {
    console.error(e);
    process.exit(1);
  })
  .finally(() => prisma.$disconnect());
