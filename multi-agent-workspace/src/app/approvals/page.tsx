'use client';

import { ApprovalList } from '@/components/ApprovalList';

export default function ApprovalsPage() {
  return (
    <div className="mx-auto max-w-4xl p-6">
      <h1 className="mb-1 text-lg font-semibold">Approval queue</h1>
      <p className="mb-5 text-xs text-ink-muted">
        Agents pause and wait here before running gated actions (file writes with approval enabled, deletions, budget increases).
      </p>
      <ApprovalList />
    </div>
  );
}
