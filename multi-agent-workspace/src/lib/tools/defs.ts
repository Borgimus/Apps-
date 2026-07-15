import { z } from 'zod';
import { ToolDef } from '../providers/types';

/**
 * Permissioned tool registry. Every tool declares:
 *  - a zod input schema (validated before execution)
 *  - a JSON Schema exposed to models
 *  - a risk level and whether it can require human approval
 */

export type RiskLevel = 'low' | 'medium' | 'high';

export interface ToolSpec {
  name: string;
  description: string;
  risk: RiskLevel;
  /** Tools that may be gated behind human approval (per agent permissions). */
  approvable: boolean;
  /** True for tools that end the current agent run. */
  terminal?: boolean;
  input: z.ZodTypeAny;
  jsonSchema: Record<string, unknown>;
}

const str = (description: string) => ({ type: 'string', description });

export const TOOL_SPECS: Record<string, ToolSpec> = {
  list_files: {
    name: 'list_files',
    description: 'List all files in the project workspace with their latest version numbers.',
    risk: 'low',
    approvable: false,
    input: z.object({}).passthrough(),
    jsonSchema: { type: 'object', properties: {}, additionalProperties: false },
  },
  read_file: {
    name: 'read_file',
    description: 'Read the latest version of a project file.',
    risk: 'low',
    approvable: false,
    input: z.object({ path: z.string().min(1) }),
    jsonSchema: {
      type: 'object',
      properties: { path: str('Project-relative file path, e.g. "src/app.js"') },
      required: ['path'],
    },
  },
  write_file: {
    name: 'write_file',
    description:
      'Create or update a project file. Creates a new immutable version; history is preserved. ' +
      'If the file already exists, pass baseVersion (the version you read) to avoid conflicting with another agent.',
    risk: 'medium',
    approvable: true,
    input: z.object({
      path: z.string().min(1),
      content: z.string(),
      note: z.string().optional(),
      baseVersion: z.number().int().optional(),
    }),
    jsonSchema: {
      type: 'object',
      properties: {
        path: str('Project-relative file path'),
        content: str('Full new file content'),
        note: str('Short change note for the version history'),
        baseVersion: { type: 'integer', description: 'Version you last read (conflict detection)' },
      },
      required: ['path', 'content'],
    },
  },
  delete_file: {
    name: 'delete_file',
    description: 'Mark a project file as deleted. History is preserved and the file can be restored by the user.',
    risk: 'high',
    approvable: true,
    input: z.object({ path: z.string().min(1), reason: z.string().optional() }),
    jsonSchema: {
      type: 'object',
      properties: { path: str('File to delete'), reason: str('Why it should be deleted') },
      required: ['path'],
    },
  },
  create_task: {
    name: 'create_task',
    description:
      'Create a new task (or subtask) and optionally delegate it to another agent on the project by role.',
    risk: 'low',
    approvable: false,
    input: z.object({
      title: z.string().min(1),
      description: z.string().default(''),
      acceptanceCriteria: z.string().optional(),
      ownerRole: z.string().optional(),
      reviewerRole: z.string().optional(),
      priority: z.enum(['low', 'medium', 'high', 'critical']).optional(),
      parentTaskId: z.string().optional(),
    }),
    jsonSchema: {
      type: 'object',
      properties: {
        title: str('Task title'),
        description: str('What needs to be done'),
        acceptanceCriteria: str('How completion is judged'),
        ownerRole: str('Role of the project agent to assign as owner, e.g. "Developer"'),
        reviewerRole: str('Role of the project agent who must review the output'),
        priority: { type: 'string', enum: ['low', 'medium', 'high', 'critical'] },
        parentTaskId: str('Parent task id if this is a subtask'),
      },
      required: ['title'],
    },
  },
  update_task: {
    name: 'update_task',
    description: 'Update the status or result summary of a task you own.',
    risk: 'low',
    approvable: false,
    input: z.object({
      taskId: z.string().min(1),
      status: z
        .enum(['ready', 'in_progress', 'blocked', 'awaiting_review', 'completed', 'failed'])
        .optional(),
      resultSummary: z.string().optional(),
    }),
    jsonSchema: {
      type: 'object',
      properties: {
        taskId: str('Task id'),
        status: { type: 'string', enum: ['ready', 'in_progress', 'blocked', 'awaiting_review', 'completed', 'failed'] },
        resultSummary: str('Result or progress summary'),
      },
      required: ['taskId'],
    },
  },
  send_message: {
    name: 'send_message',
    description:
      'Send a structured message to the project (optionally addressed to a specific agent by name or role).',
    risk: 'low',
    approvable: false,
    input: z.object({
      to: z.string().optional(),
      type: z
        .enum([
          'task_assignment', 'status_update', 'question', 'answer', 'review_request',
          'review_result', 'decision_proposal', 'objection', 'handoff', 'blocker',
          'completion_report', 'comment',
        ])
        .default('status_update'),
      content: z.string().min(1),
    }),
    jsonSchema: {
      type: 'object',
      properties: {
        to: str('Recipient agent name or role (omit to broadcast)'),
        type: {
          type: 'string',
          enum: [
            'task_assignment', 'status_update', 'question', 'answer', 'review_request',
            'review_result', 'decision_proposal', 'objection', 'handoff', 'blocker',
            'completion_report', 'comment',
          ],
        },
        content: str('Message body'),
      },
      required: ['content'],
    },
  },
  record_decision: {
    name: 'record_decision',
    description: 'Record a project decision in the decision log.',
    risk: 'low',
    approvable: false,
    input: z.object({ title: z.string().min(1), detail: z.string().default('') }),
    jsonSchema: {
      type: 'object',
      properties: { title: str('Decision title'), detail: str('Context, options considered, rationale') },
      required: ['title'],
    },
  },
  request_review: {
    name: 'request_review',
    description: 'Request that another agent review a task output. Moves the task to awaiting_review.',
    risk: 'low',
    approvable: false,
    input: z.object({
      taskId: z.string().optional(),
      reviewerRole: z.string().min(1),
      note: z.string().default(''),
    }),
    jsonSchema: {
      type: 'object',
      properties: {
        taskId: str('Task to review (defaults to the current task)'),
        reviewerRole: str('Name or role of the reviewing agent'),
        note: str('What the reviewer should focus on'),
      },
      required: ['reviewerRole'],
    },
  },
  request_approval: {
    name: 'request_approval',
    description: 'Ask the human operator to approve an action only when no automatic tool approval gate exists. Never call this before an approvable tool.',
    risk: 'medium',
    approvable: false,
    input: z.object({ action: z.string().min(1), reason: z.string().min(1) }),
    jsonSchema: {
      type: 'object',
      properties: { action: str('The action needing approval'), reason: str('Why it is needed and its risks') },
      required: ['action', 'reason'],
    },
  },
  complete_task: {
    name: 'complete_task',
    description:
      'Finish your current run. If the task has an assigned reviewer, it moves to awaiting_review; otherwise it is completed.',
    risk: 'low',
    approvable: false,
    terminal: true,
    input: z.object({ summary: z.string().min(1) }),
    jsonSchema: {
      type: 'object',
      properties: { summary: str('Concise summary of what was accomplished') },
      required: ['summary'],
    },
  },
};

// ---------------------------------------------------------------------------
// GitHub tools — read tools need the githubRead permission; write tools need
// githubWrite (or githubPullRequest) AND unconditional human approval.
// ---------------------------------------------------------------------------

const GITHUB_SPECS: Record<string, ToolSpec> = {
  github_list_tree: {
    name: 'github_list_tree',
    description: 'List files in the connected GitHub repository at a branch/ref (optionally under a path prefix).',
    risk: 'low',
    approvable: false,
    input: z.object({ ref: z.string().optional(), path: z.string().optional() }),
    jsonSchema: {
      type: 'object',
      properties: { ref: str('Branch or ref (defaults to the configured base branch)'), path: str('Only list entries under this path prefix') },
    },
  },
  github_read_file: {
    name: 'github_read_file',
    description: 'Read a file from the connected GitHub repository.',
    risk: 'low',
    approvable: false,
    input: z.object({ path: z.string().min(1), ref: z.string().optional() }),
    jsonSchema: {
      type: 'object',
      properties: { path: str('Repository-relative file path'), ref: str('Branch or ref (defaults to the base branch)') },
      required: ['path'],
    },
  },
  github_search_code: {
    name: 'github_search_code',
    description: 'Search code in the connected GitHub repository.',
    risk: 'low',
    approvable: false,
    input: z.object({ query: z.string().min(1) }),
    jsonSchema: { type: 'object', properties: { query: str('Search terms (GitHub code-search syntax)') }, required: ['query'] },
  },
  github_read_branch: {
    name: 'github_read_branch',
    description: 'Read metadata about a branch in the connected repository (head SHA, last commit, protection).',
    risk: 'low',
    approvable: false,
    input: z.object({ branch: z.string().min(1) }),
    jsonSchema: { type: 'object', properties: { branch: str('Branch name') }, required: ['branch'] },
  },
  github_read_pull_request: {
    name: 'github_read_pull_request',
    description: 'Read a pull request in the connected repository (title, body, branches, stats).',
    risk: 'low',
    approvable: false,
    input: z.object({ number: z.number().int().positive() }),
    jsonSchema: { type: 'object', properties: { number: { type: 'integer', description: 'PR number' } }, required: ['number'] },
  },
  github_read_diff: {
    name: 'github_read_diff',
    description: 'Read a unified diff: either of a pull request (number) or between two refs (base...head).',
    risk: 'low',
    approvable: false,
    input: z.object({ number: z.number().int().positive().optional(), base: z.string().optional(), head: z.string().optional() })
      .refine((v) => v.number !== undefined || v.head !== undefined, 'Provide number or head'),
    jsonSchema: {
      type: 'object',
      properties: {
        number: { type: 'integer', description: 'PR number (alternative to base/head)' },
        base: str('Base ref (defaults to the configured base branch)'),
        head: str('Head ref to compare'),
      },
    },
  },
  github_read_checks: {
    name: 'github_read_checks',
    description: 'Read CI check runs for a commit or branch head in the connected repository.',
    risk: 'low',
    approvable: false,
    input: z.object({ ref: z.string().min(1) }),
    jsonSchema: { type: 'object', properties: { ref: str('Commit SHA or branch name') }, required: ['ref'] },
  },
  github_create_branch: {
    name: 'github_create_branch',
    description:
      'Create a new branch in the connected repository. The workspace automatically pauses for human approval; do not call request_approval separately. Branch name MUST start with agent/, agents/ or feature/.',
    risk: 'high',
    approvable: true,
    input: z.object({ branch: z.string().min(1), fromBranch: z.string().optional() }),
    jsonSchema: {
      type: 'object',
      properties: { branch: str('New branch name (agent/*, agents/* or feature/*)'), fromBranch: str('Source branch (defaults to the base branch)') },
      required: ['branch'],
    },
  },
  github_write_file: {
    name: 'github_write_file',
    description:
      'Create or update ONE file on an agent branch (agent/*, agents/*, feature/*) as a commit. The workspace automatically pauses for human approval; do not call request_approval separately. Protected branches and workflow files are always refused.',
    risk: 'high',
    approvable: true,
    input: z.object({ branch: z.string().min(1).max(200), path: z.string().min(1).max(500), content: z.string().max(500_000), message: z.string().max(200).optional() }),
    jsonSchema: {
      type: 'object',
      properties: {
        branch: str('Target agent branch'), path: str('Repository-relative file path'),
        content: str('Full new file content'), message: str('Commit message'),
      },
      required: ['branch', 'path', 'content'],
    },
  },
  github_commit_files: {
    name: 'github_commit_files',
    description:
      'Commit MULTIPLE files to an agent branch in one commit. The workspace automatically pauses for human approval; do not call request_approval separately. Protected branches and workflow files are always refused.',
    risk: 'high',
    approvable: true,
    input: z.object({
      branch: z.string().min(1).max(200),
      message: z.string().min(1).max(200),
      files: z.array(z.object({ path: z.string().min(1).max(500), content: z.string().max(500_000) })).min(1).max(20),
    }),
    jsonSchema: {
      type: 'object',
      properties: {
        branch: str('Target agent branch'), message: str('Commit message'),
        files: {
          type: 'array',
          minItems: 1,
          maxItems: 20,
          description: 'Files to include in the commit',
          items: { type: 'object', properties: { path: str('File path'), content: str('Full file content') }, required: ['path', 'content'] },
        },
      },
      required: ['branch', 'message', 'files'],
    },
  },
  github_open_draft_pull_request: {
    name: 'github_open_draft_pull_request',
    description:
      'Open a DRAFT pull request from an agent branch. The workspace automatically pauses for human approval; do not call request_approval separately. Merging is never available to agents.',
    risk: 'high',
    approvable: true,
    input: z.object({ head: z.string().min(1), title: z.string().min(1), body: z.string().optional(), base: z.string().optional() }),
    jsonSchema: {
      type: 'object',
      properties: {
        head: str('Source agent branch'), title: str('PR title'),
        body: str('PR description'), base: str('Target branch (defaults to the configured base branch)'),
      },
      required: ['head', 'title'],
    },
  },
};

Object.assign(TOOL_SPECS, GITHUB_SPECS);

export function toolDefsFor(allowedTools: string[]): ToolDef[] {
  return allowedTools
    .map((name) => TOOL_SPECS[name])
    .filter((s): s is ToolSpec => Boolean(s))
    .map((s) => ({ name: s.name, description: s.description, inputSchema: s.jsonSchema }));
}

export const ALL_TOOL_NAMES = Object.keys(TOOL_SPECS);
