import { prisma } from '../db';
import { emitActivity, notify } from '../events';
import { GithubError, redactSecretsFromText } from '../github/auth';
import { GITHUB_READ_TOOLS, GITHUB_WRITE_TOOLS, runGithubTool } from '../github/tools';
import { parseJson, toJson } from '../json';
import { TOOL_SPECS } from './defs';

export interface ToolContext {
  projectId: string;
  agentId: string;
  agentName: string;
  runId: string;
  taskId?: string | null;
  modelCallId?: string | null;
  allowedTools: string[];
}

export interface ToolResult {
  ok: boolean;
  output: unknown;
  error?: string;
  /** Set by complete_task so the engine can finalize the run. */
  terminal?: { summary: string };
}

export interface AgentPermissions {
  fileWrite?: boolean;
  fileWriteRequiresApproval?: boolean;
  fileDeleteRequiresApproval?: boolean;
  network?: boolean;
  githubRead?: boolean;
  githubWrite?: boolean;
  githubPullRequest?: boolean;
}

/** Does this tool call require a human approval gate before executing? */
export function toolNeedsApproval(
  toolName: string,
  permissions: AgentPermissions,
): boolean {
  const spec = TOOL_SPECS[toolName];
  if (!spec || !spec.approvable) return false;
  // GitHub mutations ALWAYS require human approval — the githubWrite /
  // githubPullRequest permissions let an agent request them, never bypass this.
  if (GITHUB_WRITE_TOOLS.has(toolName)) return true;
  if (toolName === 'write_file') return permissions.fileWriteRequiresApproval === true;
  if (toolName === 'delete_file') return permissions.fileDeleteRequiresApproval !== false; // default: gated
  return spec.risk === 'high';
}

/** Which permission flag (if any) a GitHub tool requires. */
function requiredGithubPermission(toolName: string): keyof AgentPermissions | null {
  if (toolName === 'github_open_draft_pull_request') return 'githubPullRequest';
  if (GITHUB_WRITE_TOOLS.has(toolName)) return 'githubWrite';
  if (GITHUB_READ_TOOLS.has(toolName)) return 'githubRead';
  return null;
}

async function resolveProjectAgent(projectId: string, roleOrName: string) {
  const links = await prisma.projectAgent.findMany({
    where: { projectId },
    include: { agent: true },
  });
  const needle = roleOrName.trim().toLowerCase();
  return (
    links.find((l) => l.agent.name.toLowerCase() === needle) ??
    links.find((l) => l.agent.role.toLowerCase() === needle) ??
    links.find((l) => l.agent.role.toLowerCase().includes(needle)) ??
    null
  )?.agent ?? null;
}

/**
 * Validate and execute a tool call. Permission checks happen here — agents
 * cannot bypass them because this is the only execution path.
 * Every call is recorded as an immutable ToolCall row + activity event.
 */
export async function executeTool(
  ctx: ToolContext,
  toolName: string,
  rawInput: unknown,
): Promise<ToolResult> {
  const startedAt = Date.now();
  const spec = TOOL_SPECS[toolName];

  const record = async (result: ToolResult, status: string) => {
    await prisma.toolCall.create({
      data: {
        runId: ctx.runId,
        modelCallId: ctx.modelCallId ?? null,
        projectId: ctx.projectId,
        agentId: ctx.agentId,
        toolName,
        inputJson: toJson(rawInput),
        outputJson: toJson(result.output),
        status,
        error: result.error ?? null,
        riskLevel: spec?.risk ?? 'low',
        durationMs: Date.now() - startedAt,
      },
    });
    await emitActivity({
      projectId: ctx.projectId,
      actor: ctx.agentId,
      type: 'tool_call',
      summary: `${ctx.agentName} → ${toolName} (${status})`,
      data: { toolName, input: rawInput, output: result.output, status, error: result.error },
      refId: ctx.runId,
    });
    return result;
  };

  if (!spec) {
    return record({ ok: false, output: null, error: `Unknown tool "${toolName}"` }, 'error');
  }
  if (!ctx.allowedTools.includes(toolName)) {
    return record(
      { ok: false, output: null, error: `Permission denied: agent is not allowed to use "${toolName}"` },
      'denied',
    );
  }
  const parsed = spec.input.safeParse(rawInput ?? {});
  if (!parsed.success) {
    return record(
      { ok: false, output: null, error: `Invalid input: ${parsed.error.issues.map((i) => i.message).join('; ')}` },
      'error',
    );
  }
  const input = parsed.data as Record<string, unknown>;

  try {
    const result = await runTool(ctx, toolName, input);
    return record(result, result.ok ? 'ok' : 'error');
  } catch (err) {
    return record({ ok: false, output: null, error: String(err) }, 'error');
  }
}

async function runTool(
  ctx: ToolContext,
  toolName: string,
  input: Record<string, unknown>,
): Promise<ToolResult> {
  // GitHub tools: enforce the per-agent permission flag, then delegate to the
  // repo-scoped GitHub layer. Errors are redacted before they can reach the
  // model, the timeline, or the browser.
  if (GITHUB_READ_TOOLS.has(toolName) || GITHUB_WRITE_TOOLS.has(toolName)) {
    const perm = requiredGithubPermission(toolName);
    if (perm) {
      const agent = await prisma.agent.findUnique({ where: { id: ctx.agentId }, select: { permissionsJson: true } });
      const permissions = parseJson<AgentPermissions>(agent?.permissionsJson ?? '{}', {});
      if (permissions[perm] !== true) {
        return {
          ok: false,
          output: null,
          error: `Permission denied: this agent does not have the "${perm}" permission required by ${toolName}`,
        };
      }
    }
    try {
      const output = await runGithubTool(ctx.projectId, toolName, input);
      return { ok: true, output };
    } catch (err) {
      const message = err instanceof GithubError ? err.message : redactSecretsFromText(String(err));
      return { ok: false, output: null, error: message };
    }
  }

  switch (toolName) {
    case 'list_files': {
      const files = await prisma.projectFile.findMany({
        where: { projectId: ctx.projectId, deleted: false },
        orderBy: { path: 'asc' },
      });
      return {
        ok: true,
        output: files.map((f) => ({ path: f.path, version: f.latestVersion, updatedAt: f.updatedAt })),
      };
    }

    case 'read_file': {
      const path = String(input.path);
      const file = await prisma.projectFile.findUnique({
        where: { projectId_path: { projectId: ctx.projectId, path } },
        include: { versions: { orderBy: { version: 'desc' }, take: 1 } },
      });
      if (!file || file.deleted || file.versions.length === 0) {
        return { ok: false, output: null, error: `File not found: ${path}` };
      }
      const v = file.versions[0]!;
      return { ok: true, output: { path, version: v.version, content: v.content } };
    }

    case 'write_file': {
      const path = String(input.path);
      const content = String(input.content);
      const note = typeof input.note === 'string' ? input.note : '';
      const baseVersion = typeof input.baseVersion === 'number' ? input.baseVersion : undefined;

      const existing = await prisma.projectFile.findUnique({
        where: { projectId_path: { projectId: ctx.projectId, path } },
      });
      // Optimistic concurrency: reject writes based on a stale version so two
      // agents cannot silently clobber each other's edits.
      if (existing && baseVersion !== undefined && baseVersion !== existing.latestVersion) {
        await notify({
          type: 'conflict',
          title: `Edit conflict on ${path}`,
          body: `${ctx.agentName} tried to write based on v${baseVersion}, but the file is at v${existing.latestVersion}.`,
          projectId: ctx.projectId,
        });
        return {
          ok: false,
          output: null,
          error: `Conflict: ${path} is at version ${existing.latestVersion}, but you based your edit on version ${baseVersion}. Re-read the file and merge your change.`,
        };
      }

      const file = existing
        ? await prisma.projectFile.update({
            where: { id: existing.id },
            data: { latestVersion: existing.latestVersion + 1, deleted: false },
          })
        : await prisma.projectFile.create({
            data: { projectId: ctx.projectId, path, latestVersion: 1 },
          });
      await prisma.fileVersion.create({
        data: {
          fileId: file.id,
          version: file.latestVersion,
          content,
          authorAgentId: ctx.agentId,
          runId: ctx.runId,
          note,
        },
      });
      await emitActivity({
        projectId: ctx.projectId,
        actor: ctx.agentId,
        type: 'file_change',
        summary: `${ctx.agentName} wrote ${path} (v${file.latestVersion})`,
        data: { path, version: file.latestVersion, note, bytes: content.length },
        refId: file.id,
      });
      return { ok: true, output: { path, version: file.latestVersion } };
    }

    case 'delete_file': {
      const path = String(input.path);
      const file = await prisma.projectFile.findUnique({
        where: { projectId_path: { projectId: ctx.projectId, path } },
      });
      if (!file || file.deleted) return { ok: false, output: null, error: `File not found: ${path}` };
      await prisma.projectFile.update({ where: { id: file.id }, data: { deleted: true } });
      await emitActivity({
        projectId: ctx.projectId,
        actor: ctx.agentId,
        type: 'file_change',
        summary: `${ctx.agentName} deleted ${path} (history preserved)`,
        data: { path, reason: input.reason ?? '' },
        refId: file.id,
      });
      return { ok: true, output: { path, deleted: true } };
    }

    case 'create_task': {
      const ownerRole = typeof input.ownerRole === 'string' ? input.ownerRole : undefined;
      const reviewerRole = typeof input.reviewerRole === 'string' ? input.reviewerRole : undefined;
      const owner = ownerRole ? await resolveProjectAgent(ctx.projectId, ownerRole) : null;
      const reviewer = reviewerRole ? await resolveProjectAgent(ctx.projectId, reviewerRole) : null;
      const task = await prisma.task.create({
        data: {
          projectId: ctx.projectId,
          title: String(input.title),
          description: String(input.description ?? ''),
          acceptanceCriteria: String(input.acceptanceCriteria ?? ''),
          priority: (input.priority as string) ?? 'medium',
          status: owner ? 'ready' : 'backlog',
          ownerAgentId: owner?.id ?? null,
          reviewerAgentId: reviewer?.id ?? null,
          createdBy: ctx.agentId,
          parentTaskId: (input.parentTaskId as string) ?? ctx.taskId ?? null,
        },
      });
      if (owner) {
        await prisma.message.create({
          data: {
            projectId: ctx.projectId,
            taskId: task.id,
            fromAgentId: ctx.agentId,
            toAgentId: owner.id,
            type: 'task_assignment',
            content: `You have been assigned: "${task.title}" — ${task.description}`,
            runId: ctx.runId,
          },
        });
      }
      await emitActivity({
        projectId: ctx.projectId,
        actor: ctx.agentId,
        type: 'task_created',
        summary: `${ctx.agentName} created task "${task.title}"${owner ? ` → ${owner.name}` : ''}`,
        data: { taskId: task.id, owner: owner?.name ?? null, reviewer: reviewer?.name ?? null },
        refId: task.id,
      });
      return {
        ok: true,
        output: { taskId: task.id, title: task.title, owner: owner?.name ?? null, reviewer: reviewer?.name ?? null },
      };
    }

    case 'update_task': {
      const task = await prisma.task.findUnique({ where: { id: String(input.taskId) } });
      if (!task || task.projectId !== ctx.projectId) {
        return { ok: false, output: null, error: 'Task not found in this project' };
      }
      const data: Record<string, unknown> = {};
      if (typeof input.status === 'string') data.status = input.status;
      if (typeof input.resultSummary === 'string') data.resultSummary = input.resultSummary;
      const updated = await prisma.task.update({ where: { id: task.id }, data });
      await emitActivity({
        projectId: ctx.projectId,
        actor: ctx.agentId,
        type: 'status_change',
        summary: `${ctx.agentName} set task "${task.title}" → ${updated.status}`,
        data: { taskId: task.id, status: updated.status },
        refId: task.id,
      });
      return { ok: true, output: { taskId: task.id, status: updated.status } };
    }

    case 'send_message': {
      const to = typeof input.to === 'string' ? await resolveProjectAgent(ctx.projectId, input.to) : null;
      const msg = await prisma.message.create({
        data: {
          projectId: ctx.projectId,
          taskId: ctx.taskId ?? null,
          fromAgentId: ctx.agentId,
          toAgentId: to?.id ?? null,
          type: String(input.type ?? 'status_update'),
          content: String(input.content),
          runId: ctx.runId,
        },
      });
      await emitActivity({
        projectId: ctx.projectId,
        actor: ctx.agentId,
        type: 'message',
        summary: `${ctx.agentName} → ${to?.name ?? 'project'}: ${String(input.type ?? 'status_update')}`,
        data: { messageId: msg.id, type: msg.type, to: to?.name ?? null, content: msg.content },
        refId: msg.id,
      });
      if (msg.type === 'review_result' || msg.type === 'review_request') {
        await notify({
          type: 'review_request',
          title: `${msg.type === 'review_result' ? 'Review result' : 'Review requested'} from ${ctx.agentName}`,
          body: msg.content.slice(0, 300),
          projectId: ctx.projectId,
        });
      }
      return { ok: true, output: { messageId: msg.id, to: to?.name ?? 'project' } };
    }

    case 'record_decision': {
      const d = await prisma.decision.create({
        data: {
          projectId: ctx.projectId,
          taskId: ctx.taskId ?? null,
          title: String(input.title),
          detail: String(input.detail ?? ''),
          madeBy: ctx.agentId,
        },
      });
      await emitActivity({
        projectId: ctx.projectId,
        actor: ctx.agentId,
        type: 'decision',
        summary: `${ctx.agentName} recorded decision: ${d.title}`,
        data: { decisionId: d.id, detail: d.detail },
        refId: d.id,
      });
      return { ok: true, output: { decisionId: d.id } };
    }

    case 'request_review': {
      const reviewer = await resolveProjectAgent(ctx.projectId, String(input.reviewerRole));
      if (!reviewer) return { ok: false, output: null, error: `No project agent matches "${input.reviewerRole}"` };
      const taskId = (input.taskId as string) ?? ctx.taskId;
      if (taskId) {
        await prisma.task.update({
          where: { id: taskId },
          data: { status: 'awaiting_review', reviewerAgentId: reviewer.id },
        });
      }
      await prisma.message.create({
        data: {
          projectId: ctx.projectId,
          taskId: taskId ?? null,
          fromAgentId: ctx.agentId,
          toAgentId: reviewer.id,
          type: 'review_request',
          content: String(input.note ?? '') || 'Please review my output on this task.',
          runId: ctx.runId,
        },
      });
      await notify({
        type: 'review_request',
        title: `${ctx.agentName} requested review from ${reviewer.name}`,
        projectId: ctx.projectId,
      });
      return { ok: true, output: { reviewer: reviewer.name, taskId: taskId ?? null } };
    }

    case 'request_approval': {
      const approval = await prisma.approvalRequest.create({
        data: {
          projectId: ctx.projectId,
          runId: ctx.runId,
          agentId: ctx.agentId,
          action: String(input.action),
          reason: String(input.reason),
          payloadJson: toJson(input),
          riskLevel: 'medium',
        },
      });
      await notify({
        type: 'approval_request',
        title: `${ctx.agentName} requests approval: ${String(input.action)}`,
        body: String(input.reason),
        projectId: ctx.projectId,
      });
      return { ok: true, output: { approvalId: approval.id, status: 'pending' } };
    }

    case 'complete_task': {
      const summary = String(input.summary);
      return { ok: true, output: { summary }, terminal: { summary } };
    }

    default:
      return { ok: false, output: null, error: `Tool "${toolName}" has no implementation` };
  }
}

export { parseJson };
