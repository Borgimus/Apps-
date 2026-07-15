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
    description: 'Ask the human operator to approve an action outside your permissions.',
    risk: 'medium',
    approvable: false,
    input: z.object({ action: z.string().min(1), reason: z.string().min(1) }),
    jsonSchema: {
      type: 'object',
      properties: { action: str('The action needing approval'), reason: str('Why it is needed and its risks') },
      required: ['action', 'reason'],
    },
  },
  github_list_tree: {
    name: 'github_list_tree',
    description: 'List files in the connected GitHub repository at a branch or commit. Read-only.',
    risk: 'low', approvable: false,
    input: z.object({ ref: z.string().min(1).max(200).optional() }),
    jsonSchema: { type: 'object', properties: { ref: str('Branch, tag, or commit; defaults to the project base branch') } },
  },
  github_read_file: {
    name: 'github_read_file',
    description: 'Read a UTF-8 text file from the connected GitHub repository. Read-only and limited to 300 KB.',
    risk: 'low', approvable: false,
    input: z.object({ path: z.string().min(1).max(500), ref: z.string().min(1).max(200).optional() }),
    jsonSchema: {
      type: 'object',
      properties: { path: str('Repository-relative path'), ref: str('Branch, tag, or commit') },
      required: ['path'],
    },
  },
  github_search_code: {
    name: 'github_search_code',
    description: 'Search code only within the repository connected to this project.',
    risk: 'low', approvable: false,
    input: z.object({ query: z.string().min(1).max(200) }),
    jsonSchema: { type: 'object', properties: { query: str('GitHub code-search terms') }, required: ['query'] },
  },
  github_read_branch: {
    name: 'github_read_branch',
    description: 'Read branch metadata from the connected GitHub repository.',
    risk: 'low', approvable: false,
    input: z.object({ branch: z.string().min(1).max(200).optional() }),
    jsonSchema: { type: 'object', properties: { branch: str('Branch name; defaults to the project base branch') } },
  },
  github_read_pull_request: {
    name: 'github_read_pull_request',
    description: 'Read pull-request metadata from the connected GitHub repository.',
    risk: 'low', approvable: false,
    input: z.object({ number: z.number().int().positive() }),
    jsonSchema: { type: 'object', properties: { number: { type: 'integer', minimum: 1 } }, required: ['number'] },
  },
  github_read_diff: {
    name: 'github_read_diff',
    description: 'Read the changed files and patches for a pull request in the connected repository.',
    risk: 'low', approvable: false,
    input: z.object({ number: z.number().int().positive() }),
    jsonSchema: { type: 'object', properties: { number: { type: 'integer', minimum: 1 } }, required: ['number'] },
  },
  github_read_checks: {
    name: 'github_read_checks',
    description: 'Read commit statuses and check runs for a branch or commit in the connected repository.',
    risk: 'low', approvable: false,
    input: z.object({ ref: z.string().min(1).max(200).optional() }),
    jsonSchema: { type: 'object', properties: { ref: str('Branch or commit; defaults to the project base branch') } },
  },
  github_create_branch: {
    name: 'github_create_branch',
    description: 'Create the project working branch from its base branch. Requires human approval.',
    risk: 'high', approvable: true,
    input: z.object({
      branch: z.string().min(1).max(200),
      baseRef: z.string().min(1).max(200).optional(),
    }),
    jsonSchema: {
      type: 'object',
      properties: { branch: str('New agent/, agents/, or feature/ branch'), baseRef: str('Base branch or commit') },
      required: ['branch'],
    },
  },
  github_write_file: {
    name: 'github_write_file',
    description: 'Create or replace one file on the configured working branch. Creates a commit and requires approval.',
    risk: 'high', approvable: true,
    input: z.object({
      branch: z.string().min(1).max(200).optional(),
      path: z.string().min(1).max(500),
      content: z.string().max(500_000),
      message: z.string().min(1).max(200),
      sha: z.string().optional(),
    }),
    jsonSchema: {
      type: 'object',
      properties: {
        branch: str('Configured working branch'),
        path: str('Repository-relative file path'),
        content: str('Complete UTF-8 file content'),
        message: str('Commit message'),
        sha: str('Existing blob SHA when replacing a file'),
      },
      required: ['path', 'content', 'message'],
    },
  },
  github_commit_files: {
    name: 'github_commit_files',
    description: 'Commit up to 20 complete files atomically on the configured working branch. Requires approval.',
    risk: 'high', approvable: true,
    input: z.object({
      branch: z.string().min(1).max(200).optional(),
      message: z.string().min(1).max(200),
      files: z.array(z.object({
        path: z.string().min(1).max(500),
        content: z.string().max(500_000),
      })).min(1).max(20),
    }),
    jsonSchema: {
      type: 'object',
      properties: {
        branch: str('Configured working branch'),
        message: str('Commit message'),
        files: {
          type: 'array', minItems: 1, maxItems: 20,
          items: {
            type: 'object',
            properties: { path: str('Repository-relative path'), content: str('Complete UTF-8 content') },
            required: ['path', 'content'],
          },
        },
      },
      required: ['message', 'files'],
    },
  },
  github_open_draft_pull_request: {
    name: 'github_open_draft_pull_request',
    description: 'Open a draft pull request from the configured working branch to its base branch. Requires approval.',
    risk: 'high', approvable: true,
    input: z.object({
      branch: z.string().min(1).max(200).optional(),
      title: z.string().min(1).max(200),
      body: z.string().max(30_000).default(''),
    }),
    jsonSchema: {
      type: 'object',
      properties: { branch: str('Configured working branch'), title: str('Pull-request title'), body: str('Markdown body') },
      required: ['title'],
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

export function toolDefsFor(allowedTools: string[]): ToolDef[] {
  return allowedTools
    .map((name) => TOOL_SPECS[name])
    .filter((s): s is ToolSpec => Boolean(s))
    .map((s) => ({ name: s.name, description: s.description, inputSchema: s.jsonSchema }));
}

export const ALL_TOOL_NAMES = Object.keys(TOOL_SPECS);
