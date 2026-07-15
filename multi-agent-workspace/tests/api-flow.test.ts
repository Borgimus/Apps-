import { describe, expect, it } from 'vitest';
import { prisma } from '@/lib/db';
import { GET as getProjects, POST as postProject } from '@/app/api/projects/route';
import { POST as postAgent } from '@/app/api/agents/route';
import { POST as postTask } from '@/app/api/tasks/route';
import { POST as postRun } from '@/app/api/runs/route';
import { GET as getRun } from '@/app/api/runs/[id]/route';
import { POST as controlRun } from '@/app/api/runs/[id]/control/route';
import { POST as rerunCall } from '@/app/api/model-calls/[id]/rerun/route';
import { GET as getHealth } from '@/app/api/health/route';
import { makeFixture, waitFor } from './helpers';

function jsonReq(url: string, method: string, body?: unknown): Request {
  return new Request(`http://localhost${url}`, {
    method,
    headers: { 'content-type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

async function body<T>(res: Response): Promise<{ ok: boolean; data: T; error?: string }> {
  return (await res.json()) as { ok: boolean; data: T; error?: string };
}

const params = (id: string) => ({ params: Promise.resolve({ id }) });

/**
 * End-to-end primary user flow, driven through the actual API route handlers:
 * create project → create agent → assign → create task → run agent →
 * inspect model calls → rerun a prompt → verify history.
 */
describe('primary user flow via API', () => {
  it('walks the full lifecycle', async () => {
    const f = await makeFixture(); // provides workspace + a mock model config

    // Health
    const health = await body<{ status: string }>(await getHealth());
    expect(health.data.status).toBe('healthy');

    // Create project
    const projRes = await body<{ id: string }>(
      await postProject(jsonReq('/api/projects', 'POST', { name: 'API test project', objective: 'Test the API', orchestrationMode: 'peer' })),
    );
    expect(projRes.ok).toBe(true);
    const projectId = projRes.data.id;

    // Create agent (assigned to the project)
    const agentRes = await body<{ id: string }>(
      await postAgent(
        jsonReq('/api/agents', 'POST', {
          name: 'API Agent',
          role: 'Generalist',
          systemPrompt: 'You are a test agent created via API.',
          modelConfigId: f.modelConfig.id,
          tools: ['write_file', 'read_file', 'complete_task'],
          permissions: { fileWrite: true },
          projectIds: [projectId],
        }),
      ),
    );
    expect(agentRes.ok).toBe(true);
    const agentId = agentRes.data.id;

    // Validation is enforced
    const badAgent = await postAgent(jsonReq('/api/agents', 'POST', { name: '' }));
    expect(badAgent.status).toBe(400);

    // Create task
    const taskRes = await body<{ id: string }>(
      await postTask(
        jsonReq('/api/tasks', 'POST', {
          projectId,
          title: 'Write the notes file',
          description: '[test:write] produce notes.txt',
          ownerAgentId: agentId,
        }),
      ),
    );
    expect(taskRes.ok).toBe(true);

    // Start a run on the task
    const runRes = await body<{ id: string }>(
      await postRun(
        jsonReq('/api/runs', 'POST', {
          agentId,
          projectId,
          taskId: taskRes.data.id,
          objective: '[test:write] produce notes.txt',
        }),
      ),
    );
    expect(runRes.ok).toBe(true);

    // Wait for completion, then verify the full record
    const finished = await waitFor(async () => {
      const detail = await body<{ status: string; modelCalls: Array<{ id: string }>; toolCalls: Array<{ toolName: string }> }>(
        await getRun(jsonReq(`/api/runs/${runRes.data.id}`, 'GET'), params(runRes.data.id)),
      );
      return detail.data.status === 'completed' ? detail.data : null;
    });
    expect(finished.modelCalls.length).toBeGreaterThan(0);
    expect(finished.toolCalls.map((t) => t.toolName)).toContain('write_file');

    const task = await prisma.task.findUnique({ where: { id: taskRes.data.id } });
    expect(task?.status).toBe('completed');

    // Rerun the first model call — creates a new version, original untouched
    const firstCallId = finished.modelCalls[0]!.id;
    const rerun = await body<{ id: string; version: number; parentCallId: string }>(
      await rerunCall(jsonReq(`/api/model-calls/${firstCallId}/rerun`, 'POST', { temperature: 0.7 }), params(firstCallId)),
    );
    expect(rerun.ok).toBe(true);
    expect(rerun.data.version).toBe(2);
    expect(rerun.data.parentCallId).toBe(firstCallId);
    const original = await prisma.modelCall.findUnique({ where: { id: firstCallId } });
    expect(original?.version).toBe(1); // history preserved

    // Control endpoint rejects nonsense
    const badControl = await controlRun(jsonReq(`/api/runs/${runRes.data.id}/control`, 'POST', { action: 'explode' }), params(runRes.data.id));
    expect(badControl.status).toBe(400);

    // Dashboard listing includes the project with cost/task rollups
    const list = await body<Array<{ id: string; taskCounts: { completed: number } }>>(await getProjects());
    const row = list.data.find((p) => p.id === projectId);
    expect(row?.taskCounts.completed).toBe(1);
  }, 30_000);
});
