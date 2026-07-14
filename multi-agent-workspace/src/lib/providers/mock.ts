import {
  NormToolCall,
  ProviderAdapter,
  ProviderError,
  ProviderRequest,
  ProviderResponse,
} from './types';

/**
 * Deterministic mock provider. Lets the whole workspace run — including the
 * seeded "Collaborative Software Build" demonstration and the test suite —
 * without paid API calls.
 *
 * Behavior is keyed on:
 *  - the agent role (a "ROLE:" line the orchestrator puts in the system prompt)
 *  - the iteration (number of assistant turns so far in the transcript)
 *  - test markers in the task objective, e.g. "[test:error]"
 */

function estimateTokens(text: string): number {
  return Math.max(1, Math.ceil(text.length / 4));
}

function response(text: string, toolCalls: NormToolCall[], req: ProviderRequest): ProviderResponse {
  const promptChars =
    req.system.length + req.messages.reduce((n, m) => n + ('content' in m ? m.content.length : 0), 0);
  return {
    text,
    toolCalls,
    stopReason: toolCalls.length > 0 ? 'tool_use' : 'end_turn',
    usage: {
      inputTokens: Math.ceil(promptChars / 4),
      outputTokens: estimateTokens(text + JSON.stringify(toolCalls)),
    },
  };
}

let counter = 0;
function tid(name: string): string {
  counter = (counter + 1) % 1_000_000;
  return `mock_${name}_${counter}`;
}

function call(name: string, input: unknown): NormToolCall {
  return { id: tid(name), name, input };
}

const CALCULATOR_V1 = `/**
 * Simple calculator module (v1).
 */
function add(a, b) { return a + b; }
function subtract(a, b) { return a - b; }
function multiply(a, b) { return a * b; }
function divide(a, b) { return a / b; } // BUG: no divide-by-zero guard

module.exports = { add, subtract, multiply, divide };
`;

const CALCULATOR_V2 = `/**
 * Simple calculator module (v2) — addresses review feedback:
 * divide() now guards against division by zero.
 */
function add(a, b) { return a + b; }
function subtract(a, b) { return a - b; }
function multiply(a, b) { return a * b; }
function divide(a, b) {
  if (b === 0) throw new RangeError('Division by zero');
  return a / b;
}

module.exports = { add, subtract, multiply, divide };
`;

const ARCHITECTURE_MD = `# Calculator Feature — Architecture Proposal

## Goal
Provide a small, well-tested calculator module exposing add, subtract, multiply and divide.

## Design
- Single CommonJS module: \`src/calculator.js\`
- Pure functions, no shared state
- Errors: divide must reject division by zero with a RangeError
- Tests: table-driven unit tests covering happy paths and edge cases

## Decisions
- Keep the surface minimal; no floating-point rounding layer in v1.
`;

const QA_REPORT_MD = `# QA Report — Calculator Feature

## Scope
Manual verification of src/calculator.js (v2) against acceptance criteria.

| Check | Result |
|---|---|
| add(2,3) = 5 | PASS |
| subtract(5,2) = 3 | PASS |
| multiply(3,4) = 12 | PASS |
| divide(10,2) = 5 | PASS |
| divide(1,0) throws RangeError | PASS |

## Verdict
All acceptance criteria met. No defects found in v2.
`;

export class MockAdapter implements ProviderAdapter {
  readonly id = 'mock';

  async call(req: ProviderRequest): Promise<ProviderResponse> {
    const role = /ROLE:\s*(.+)/.exec(req.system)?.[1]?.trim().toLowerCase() ?? '';
    const firstUser = req.messages.find((m) => m.role === 'user');
    const objective = firstUser && 'content' in firstUser ? firstUser.content : '';
    const iteration = req.messages.filter((m) => m.role === 'assistant').length; // 0-based
    const allowed = new Set(req.tools.map((t) => t.name));

    // ---- Test markers (deterministic behaviors for the test suite) ----
    if (objective.includes('[test:error]')) {
      throw new ProviderError('Simulated provider failure', 'overloaded', true);
    }
    if (objective.includes('[test:loop]')) {
      return response(`Thinking… (step ${iteration + 1})`, [], req);
    }
    if (objective.includes('[test:slow]')) {
      await new Promise((r) => setTimeout(r, 300));
      return response('Done after delay.', [call('complete_task', { summary: 'Slow task done.' })], req);
    }
    if (objective.includes('[test:forbidden-tool]')) {
      return response('Trying a tool I should not have.', [call('write_file', { path: 'x.txt', content: 'x' })], req);
    }
    if (objective.includes('[test:write]')) {
      if (iteration === 0) {
        return response('Writing the requested file.', [
          call('write_file', { path: 'notes.txt', content: 'hello from mock', note: 'test write' }),
        ], req);
      }
      return response('File written.', [call('complete_task', { summary: 'Wrote notes.txt.' })], req);
    }
    if (objective.includes('[test:noop]')) {
      return response('Nothing to do.', [call('complete_task', { summary: 'No-op completed.' })], req);
    }

    // ---- Role scripts for the demonstration project ----
    if (role.includes('project manager')) {
      if (objective.toLowerCase().includes('completion report')) {
        if (iteration === 0) {
          return response('Compiling the final completion report.', [
            call('write_file', {
              path: 'reports/COMPLETION_REPORT.md',
              content:
                '# Completion Report — Calculator Feature\n\n' +
                '1. Architecture proposed and recorded (docs/ARCHITECTURE.md).\n' +
                '2. Implementation delivered (src/calculator.js, v2 after review).\n' +
                '3. Code review found a divide-by-zero defect; it was fixed.\n' +
                '4. QA verified all acceptance criteria (reports/QA_REPORT.md).\n\n' +
                'The feature is complete and ready to ship.\n',
              note: 'Final PM completion report',
            }),
          ], req);
        }
        return response('Project wrapped up.', [
          call('send_message', {
            type: 'completion_report',
            content: 'All tasks complete: architecture, implementation, review fixes and QA verification are done.',
          }),
          call('complete_task', { summary: 'Final completion report written to reports/COMPLETION_REPORT.md.' }),
        ], req);
      }
      // Decomposition script
      if (iteration === 0 && allowed.has('create_task')) {
        return response(
          'Breaking the feature request into tasks: architecture, implementation with review, and QA.',
          [
            call('create_task', {
              title: 'Propose calculator architecture',
              description: 'Design the calculator module: API surface, error handling, test strategy. Write docs/ARCHITECTURE.md.',
              acceptanceCriteria: 'docs/ARCHITECTURE.md exists and records the key design decisions.',
              ownerRole: 'Software Architect',
              priority: 'high',
            }),
            call('create_task', {
              title: 'Implement calculator module',
              description: 'Implement src/calculator.js per the architecture. Request review from the Code Reviewer.',
              acceptanceCriteria: 'add/subtract/multiply/divide implemented; review passed.',
              ownerRole: 'Developer',
              reviewerRole: 'Code Reviewer',
              priority: 'high',
            }),
            call('create_task', {
              title: 'QA test the calculator',
              description: 'Verify the implementation against acceptance criteria and write reports/QA_REPORT.md.',
              acceptanceCriteria: 'QA report written; all checks pass.',
              ownerRole: 'QA Tester',
              priority: 'medium',
            }),
          ],
          req,
        );
      }
      return response('Plan is in place.', [
        call('send_message', {
          type: 'status_update',
          content: 'Feature decomposed into 3 tasks: architecture → implementation (with review) → QA.',
        }),
        call('complete_task', { summary: 'Decomposed the feature request into 3 delegated tasks.' }),
      ], req);
    }

    if (role.includes('architect')) {
      if (iteration === 0) {
        return response('Proposing the architecture and recording the decision.', [
          call('write_file', { path: 'docs/ARCHITECTURE.md', content: ARCHITECTURE_MD, note: 'Architecture proposal' }),
          call('record_decision', {
            title: 'Calculator implemented as a single pure-function module',
            detail: 'src/calculator.js exports add/subtract/multiply/divide; divide raises RangeError on zero divisor.',
          }),
        ], req);
      }
      return response('Architecture documented.', [
        call('complete_task', { summary: 'Architecture proposal written to docs/ARCHITECTURE.md and decision recorded.' }),
      ], req);
    }

    if (role.includes('developer')) {
      const isFixup = objective.toLowerCase().includes('review feedback') ||
        req.messages.some((m) => m.role === 'user' && 'content' in m && m.content.includes('RangeError'));
      if (iteration === 0) {
        return response('Reading the architecture before implementing.', [
          call('read_file', { path: 'docs/ARCHITECTURE.md' }),
        ], req);
      }
      if (iteration === 1) {
        return response(isFixup ? 'Applying the fix requested in review.' : 'Implementing the calculator module.', [
          call('write_file', {
            path: 'src/calculator.js',
            content: isFixup ? CALCULATOR_V2 : CALCULATOR_V1,
            note: isFixup ? 'Fix divide-by-zero per review' : 'Initial implementation',
          }),
        ], req);
      }
      return response('Implementation ready for review.', [
        call('complete_task', {
          summary: isFixup
            ? 'Fixed divide-by-zero guard in src/calculator.js (v2) per review feedback.'
            : 'Implemented src/calculator.js. Ready for code review.',
        }),
      ], req);
    }

    if (role.includes('reviewer')) {
      if (iteration === 0) {
        return response('Reviewing the implementation.', [call('read_file', { path: 'src/calculator.js' })], req);
      }
      const sawGuard = req.messages.some(
        (m) => m.role === 'tool' && m.content.includes("RangeError('Division by zero')"),
      );
      if (sawGuard) {
        return response('The fix looks correct.', [
          call('send_message', {
            type: 'review_result',
            content: 'APPROVED: divide() now guards against division by zero. Implementation meets the architecture.',
          }),
          call('complete_task', { summary: 'Review passed — implementation approved.' }),
        ], req);
      }
      return response('Found a defect during review.', [
        call('send_message', {
          type: 'review_result',
          content:
            'CHANGES REQUESTED: divide() does not guard against division by zero. Per the architecture it must throw RangeError when b === 0.',
        }),
        call('complete_task', { summary: 'Review complete — changes requested (missing divide-by-zero guard).' }),
      ], req);
    }

    if (role.includes('qa')) {
      if (iteration === 0) {
        return response('Inspecting the implementation before testing.', [call('read_file', { path: 'src/calculator.js' })], req);
      }
      if (iteration === 1) {
        return response('Writing the QA report.', [
          call('write_file', { path: 'reports/QA_REPORT.md', content: QA_REPORT_MD, note: 'QA verification report' }),
        ], req);
      }
      return response('QA complete.', [
        call('complete_task', { summary: 'All acceptance criteria verified. QA report written to reports/QA_REPORT.md.' }),
      ], req);
    }

    // ---- Generic fallback: acknowledge and complete ----
    if (iteration === 0 && allowed.has('complete_task')) {
      return response(`Acknowledged: ${objective.slice(0, 120)}`, [
        call('complete_task', { summary: `Handled: ${objective.slice(0, 120)}` }),
      ], req);
    }
    return response('Task acknowledged and complete.', [], req);
  }
}
