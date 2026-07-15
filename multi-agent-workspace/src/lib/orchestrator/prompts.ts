import { z } from 'zod';

/**
 * Structured collaboration protocol. Workflow decisions are driven by this
 * validated schema — never by free-form text.
 */
export const AgentOutputSchema = z.object({
  summary: z.string(),
  workProduct: z.string(),
  status: z.enum(['completed', 'needs_review', 'needs_revision', 'blocked', 'needs_user_input']),
  nextAction: z.enum(['send_to_reviewer', 'return_to_author', 'create_subtask', 'request_user_input', 'finalize']),
  recommendedAgentRole: z.string().optional(),
  questions: z.array(z.string()).default([]),
  issues: z.array(z.string()).default([]),
  acceptanceCriteriaResults: z
    .array(z.object({ criterion: z.string(), passed: z.boolean(), evidence: z.string() }))
    .default([]),
});

export type AgentOutput = z.infer<typeof AgentOutputSchema>;

const JSON_CONTRACT = `Respond with a single JSON object only — no prose, no markdown fences. Schema:
{
  "summary": string,                  // 1-3 sentences on what you produced
  "workProduct": string,              // the actual deliverable content
  "status": "completed" | "needs_review" | "needs_revision" | "blocked" | "needs_user_input",
  "nextAction": "send_to_reviewer" | "return_to_author" | "create_subtask" | "request_user_input" | "finalize",
  "recommendedAgentRole"?: string,
  "questions": string[],
  "issues": string[],                 // concrete problems found (reviews/verification)
  "acceptanceCriteriaResults": [{ "criterion": string, "passed": boolean, "evidence": string }]
}`;

export interface PhasePromptInput {
  phase: 'initial' | 'review' | 'synthesis' | 'verification' | 'revision';
  originalPrompt: string;
  projectName: string;
  projectInstructions: string;
  agentName: string;
  otherAgentName?: string;
  otherAgentOutput?: string;
  agentAName?: string;
  agentAOutput?: string;
  agentBName?: string;
  agentBOutput?: string;
  agentAReview?: string;
  agentBReview?: string;
  previousOutput?: string;
  reviewOutput?: string;
  iteration?: number;
}

/** Explicit prompt builders — every follow-up call carries the relevant prior work. */
export function buildPhasePrompt(input: PhasePromptInput): { system: string; user: string } {
  const system = [
    `COLLABORATION PHASE: ${input.phase}`,
    `You are "${input.agentName}", collaborating with other AI agents on the project "${input.projectName}".`,
    input.projectInstructions ? `Project instructions: ${input.projectInstructions}` : '',
    '',
    JSON_CONTRACT,
  ]
    .filter(Boolean)
    .join('\n');

  let user: string;
  switch (input.phase) {
    case 'initial':
      user = [
        'Produce your best independent response to this project request. Another agent will respond independently as well; your outputs will be cross-reviewed and synthesized.',
        '',
        'Original project request:',
        input.originalPrompt,
        '',
        'Set status to "needs_review" and nextAction to "send_to_reviewer".',
      ].join('\n');
      break;

    case 'review':
      user = [
        'You are acting as the reviewing agent.',
        '',
        'Original project request:',
        input.originalPrompt,
        '',
        `Response produced by ${input.otherAgentName}:`,
        input.otherAgentOutput ?? '(missing)',
        '',
        'Review the response for correctness, completeness, feasibility, security, and alignment with the request. List concrete strengths in "summary" and concrete problems in "issues". Put your recommended changes in "workProduct".',
        'Return structured JSON only.',
      ].join('\n');
      break;

    case 'synthesis':
      user = [
        'You are the lead synthesis agent.',
        '',
        'Original project request:',
        input.originalPrompt,
        '',
        `${input.agentAName} response:`,
        input.agentAOutput ?? '(missing)',
        '',
        `${input.agentBName} response:`,
        input.agentBOutput ?? '(missing)',
        '',
        `Review of ${input.agentBName}'s response (by ${input.agentAName}):`,
        input.agentAReview ?? '(missing)',
        '',
        `Review of ${input.agentAName}'s response (by ${input.agentBName}):`,
        input.agentBReview ?? '(missing)',
        '',
        'Create one final result that preserves the strongest parts, resolves conflicts, and addresses every valid review issue. Put the complete combined deliverable in "workProduct".',
        'Set status to "needs_review" and nextAction to "send_to_reviewer".',
        'Return structured JSON only.',
      ].join('\n');
      break;

    case 'verification':
      user = [
        `You are the verifying agent${input.iteration ? ` (revision cycle ${input.iteration})` : ''}.`,
        '',
        'Original project request:',
        input.originalPrompt,
        '',
        'Synthesized result to verify:',
        input.previousOutput ?? '(missing)',
        '',
        'Verify the result against the original request. If it is acceptable, set status to "completed" and nextAction to "finalize".',
        'If it needs changes, set status to "needs_revision", nextAction to "return_to_author", and list every required change in "issues".',
        'If work cannot proceed, use "blocked" or "needs_user_input" with your questions.',
        'Return structured JSON only.',
      ].join('\n');
      break;

    case 'revision':
      user = [
        'You are revising your previous work.',
        '',
        'Original project request:',
        input.originalPrompt,
        '',
        'Your previous response:',
        input.previousOutput ?? '(missing)',
        '',
        'Reviewer feedback:',
        input.reviewOutput ?? '(missing)',
        '',
        'Produce a revised result that directly addresses every valid issue. Put the full revised deliverable in "workProduct" (this is a revised result).',
        'Set status to "needs_review" and nextAction to "send_to_reviewer".',
        'Return structured JSON only.',
      ].join('\n');
      break;
  }
  return { system, user };
}

export function buildRepairPrompt(raw: string): { system: string; user: string } {
  return {
    system: `You fix malformed JSON. ${JSON_CONTRACT}`,
    user: [
      'Fix the following so it is a single valid JSON object matching the schema exactly. Preserve the content; do not invent new information. Return JSON only.',
      '',
      raw,
    ].join('\n'),
  };
}

/** Extract the first JSON object from a possibly noisy model response. */
export function extractJson(text: string): string {
  const trimmed = text.trim().replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/, '');
  const start = trimmed.indexOf('{');
  const end = trimmed.lastIndexOf('}');
  if (start >= 0 && end > start) return trimmed.slice(start, end + 1);
  return trimmed;
}
