'use client';

import { UsagePanel } from '@/components/UsagePanel';

export default function UsagePage() {
  return (
    <div className="mx-auto max-w-5xl p-6">
      <h1 className="mb-1 text-lg font-semibold">Usage & cost</h1>
      <p className="mb-5 text-xs text-ink-muted">Token and cost accounting across all projects, agents and models.</p>
      <UsagePanel />
    </div>
  );
}
