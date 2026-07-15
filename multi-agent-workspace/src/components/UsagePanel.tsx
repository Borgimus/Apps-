'use client';

import { useApi } from './hooks';
import { Card, Spinner, usd } from './ui';

interface Rollup { costUsd: number; inputTokens: number; outputTokens: number; calls: number }
interface UsageData {
  totals: Rollup;
  byProject: Record<string, Rollup>;
  byAgent: Record<string, Rollup>;
  byModel: Record<string, Rollup>;
  byDay: Record<string, Rollup>;
}

function RollupTable({ title, data }: { title: string; data: Record<string, Rollup> }) {
  const rows = Object.entries(data).sort((a, b) => b[1].costUsd - a[1].costUsd);
  return (
    <Card>
      <h3 className="mb-2 text-xs font-semibold">{title}</h3>
      {rows.length === 0 ? (
        <p className="text-2xs text-ink-faint">No usage recorded yet.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-2xs">
            <thead>
              <tr className="border-b border-line text-left text-ink-faint">
                <th className="py-1 pr-2 font-medium">Name</th>
                <th className="py-1 pr-2 text-right font-medium">Calls</th>
                <th className="py-1 pr-2 text-right font-medium">In tok</th>
                <th className="py-1 pr-2 text-right font-medium">Out tok</th>
                <th className="py-1 text-right font-medium">Cost</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(([name, r]) => (
                <tr key={name} className="border-b border-line/40 last:border-0">
                  <td className="py-1 pr-2">{name}</td>
                  <td className="py-1 pr-2 text-right">{r.calls}</td>
                  <td className="py-1 pr-2 text-right">{r.inputTokens.toLocaleString()}</td>
                  <td className="py-1 pr-2 text-right">{r.outputTokens.toLocaleString()}</td>
                  <td className="py-1 text-right font-medium">{usd(r.costUsd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

export function UsagePanel({ projectId }: { projectId?: string }) {
  const url = projectId ? `/api/usage?projectId=${projectId}` : '/api/usage';
  const { data, loading } = useApi<UsageData>(url, 10000);
  if (loading || !data) return <Spinner />;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {[
          { label: 'Total cost', value: usd(data.totals.costUsd) },
          { label: 'Model calls', value: String(data.totals.calls) },
          { label: 'Input tokens', value: data.totals.inputTokens.toLocaleString() },
          { label: 'Output tokens', value: data.totals.outputTokens.toLocaleString() },
        ].map((s) => (
          <Card key={s.label} className="text-center">
            <p className="text-base font-semibold">{s.value}</p>
            <p className="text-2xs text-ink-faint">{s.label}</p>
          </Card>
        ))}
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {!projectId && <RollupTable title="By project" data={data.byProject} />}
        <RollupTable title="By agent" data={data.byAgent} />
        <RollupTable title="By model" data={data.byModel} />
        <RollupTable title="By day" data={data.byDay} />
      </div>
    </div>
  );
}
