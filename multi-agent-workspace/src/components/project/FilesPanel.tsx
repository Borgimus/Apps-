'use client';

import { useState } from 'react';
import { apiCall, useApi } from '../hooks';
import { Badge, Button, EmptyState, Spinner, timeAgo } from '../ui';

interface FileRow { id: string; path: string; latestVersion: number; updatedAt: string }
interface FileDetail {
  id: string;
  path: string;
  latestVersion: number;
  versions: Array<{ id: string; version: number; content: string; author: string; note: string; createdAt: string }>;
}

/** Naive line diff: common prefix/suffix, middle shown as removed/added. */
function simpleDiff(a: string, b: string): Array<{ kind: 'same' | 'del' | 'add'; line: string }> {
  const A = a.split('\n');
  const B = b.split('\n');
  let start = 0;
  while (start < A.length && start < B.length && A[start] === B[start]) start++;
  let endA = A.length - 1;
  let endB = B.length - 1;
  while (endA >= start && endB >= start && A[endA] === B[endB]) { endA--; endB--; }
  const out: Array<{ kind: 'same' | 'del' | 'add'; line: string }> = [];
  for (let i = Math.max(0, start - 3); i < start; i++) out.push({ kind: 'same', line: A[i] ?? '' });
  for (let i = start; i <= endA; i++) out.push({ kind: 'del', line: A[i] ?? '' });
  for (let i = start; i <= endB; i++) out.push({ kind: 'add', line: B[i] ?? '' });
  for (let i = endA + 1; i < Math.min(A.length, endA + 4); i++) out.push({ kind: 'same', line: A[i] ?? '' });
  return out;
}

export function FilesPanel({ files, onChanged }: { files: FileRow[]; onChanged: () => void }) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [versionView, setVersionView] = useState<number | null>(null);
  const [showDiff, setShowDiff] = useState(false);
  const { data: detail, loading, refresh } = useApi<FileDetail>(selectedId ? `/api/files/${selectedId}` : null);

  const restore = async (version: number) => {
    if (!selectedId) return;
    if (!window.confirm(`Restore v${version} as a new latest version? History is preserved.`)) return;
    await apiCall(`/api/files/${selectedId}`, 'PATCH', { restoreVersion: version });
    await refresh();
    onChanged();
  };

  if (files.length === 0) return <EmptyState title="No files yet" hint="Agents create files with the write_file tool; every version is kept." />;

  const current = detail?.versions.find((v) => v.version === (versionView ?? detail.latestVersion));
  const previous = detail?.versions.find((v) => v.version === (current?.version ?? 1) - 1);

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
      <div className="space-y-1">
        {files.map((f) => (
          <button
            key={f.id}
            onClick={() => { setSelectedId(f.id); setVersionView(null); setShowDiff(false); }}
            className={`flex w-full items-center justify-between rounded-md border px-3 py-2 text-left text-xs ${
              selectedId === f.id ? 'border-accent bg-accent-soft/40' : 'border-line bg-surface-raised hover:border-accent/50'
            }`}
          >
            <span className="truncate font-mono">{f.path}</span>
            <span className="ml-2 whitespace-nowrap text-2xs text-ink-faint">v{f.latestVersion} · {timeAgo(f.updatedAt)}</span>
          </button>
        ))}
      </div>

      <div className="lg:col-span-2">
        {!selectedId && <EmptyState title="Select a file" hint="Preview content, browse versions, view diffs, restore." />}
        {selectedId && loading && <Spinner />}
        {detail && current && (
          <div className="rounded-lg border border-line bg-surface-raised">
            <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
              <span className="font-mono text-xs font-medium">{detail.path}</span>
              <select
                className="rounded border border-line bg-surface px-1.5 py-0.5 text-2xs"
                value={current.version}
                onChange={(e) => setVersionView(Number(e.target.value))}
              >
                {detail.versions.map((v) => (
                  <option key={v.id} value={v.version}>v{v.version} — {v.author}{v.note ? ` · ${v.note}` : ''}</option>
                ))}
              </select>
              {previous && (
                <Button variant="ghost" onClick={() => setShowDiff(!showDiff)}>{showDiff ? 'View content' : `Diff vs v${previous.version}`}</Button>
              )}
              {current.version !== detail.latestVersion && (
                <Button variant="ghost" onClick={() => void restore(current.version)}>Restore this version</Button>
              )}
              <a
                className="ml-auto text-2xs font-medium text-accent hover:underline"
                href={`data:text/plain;charset=utf-8,${encodeURIComponent(current.content)}`}
                download={detail.path.split('/').pop()}
              >
                Download
              </a>
            </div>
            <div className="px-3 py-1 text-2xs text-ink-faint">
              <Badge status="gray" label={`author: ${current.author}`} /> {current.note && <span className="ml-1">“{current.note}”</span>}
              <span className="ml-2">{new Date(current.createdAt).toLocaleString()}</span>
            </div>
            {showDiff && previous ? (
              <pre className="code-block m-3">
                {simpleDiff(previous.content, current.content).map((d, i) => (
                  <div
                    key={i}
                    className={
                      d.kind === 'add' ? 'bg-emerald-100 text-emerald-900 dark:bg-emerald-900/40 dark:text-emerald-200'
                      : d.kind === 'del' ? 'bg-rose-100 text-rose-900 line-through dark:bg-rose-900/40 dark:text-rose-200'
                      : ''
                    }
                  >
                    {d.kind === 'add' ? '+ ' : d.kind === 'del' ? '− ' : '  '}{d.line}
                  </div>
                ))}
              </pre>
            ) : (
              <pre className="code-block m-3">{current.content}</pre>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
