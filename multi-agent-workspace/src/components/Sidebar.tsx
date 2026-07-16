'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useEffect, useState } from 'react';
import { useApi, apiCall } from './hooks';
import { cls } from './ui';

const NAV = [
  { href: '/', label: 'Dashboard', icon: '▦' },
  { href: '/trading', label: 'Trading', icon: '↗' },
  { href: '/agents', label: 'Agents', icon: '◉' },
  { href: '/approvals', label: 'Approvals', icon: '✓' },
  { href: '/usage', label: 'Usage', icon: '∑' },
  { href: '/settings', label: 'Settings', icon: '⚙' },
];

export function Sidebar() {
  const pathname = usePathname();
  const { data: workspace } = useApi<{ name: string } | null>('/api/workspace');
  const { data: notif, refresh } = useApi<{ unread: number; notifications: Array<{ id: string; title: string; type: string; createdAt: string; read: boolean }> }>(
    '/api/notifications',
    8000,
  );
  const { data: approvals } = useApi<Array<unknown>>('/api/approvals?status=pending', 8000);
  const [dark, setDark] = useState(false);
  const [showNotif, setShowNotif] = useState(false);

  useEffect(() => {
    const stored = localStorage.getItem('theme');
    const preferDark = stored ? stored === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches;
    setDark(preferDark);
    document.documentElement.classList.toggle('dark', preferDark);
  }, []);

  const toggleTheme = () => {
    const next = !dark;
    setDark(next);
    localStorage.setItem('theme', next ? 'dark' : 'light');
    document.documentElement.classList.toggle('dark', next);
  };

  const pendingApprovals = approvals?.length ?? 0;

  return (
    <aside className="flex w-52 shrink-0 flex-col border-r border-line bg-surface-raised">
      <div className="border-b border-line px-4 py-3">
        <p className="text-sm font-semibold">{workspace?.name ?? 'Workspace'}</p>
        <p className="text-2xs text-ink-faint">Multi-Agent Workspace</p>
      </div>
      <nav className="flex-1 space-y-0.5 p-2">
        {NAV.map((item) => {
          const active = item.href === '/' ? pathname === '/' : pathname.startsWith(item.href);
          const badge =
            item.href === '/approvals' && pendingApprovals > 0 ? (
              <span className="ml-auto rounded-full bg-amber-500 px-1.5 text-2xs font-semibold text-white">{pendingApprovals}</span>
            ) : null;
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cls(
                'flex items-center gap-2 rounded-md px-3 py-2 text-xs font-medium',
                active ? 'bg-accent-soft text-accent' : 'text-ink-muted hover:bg-surface-sunken hover:text-ink',
              )}
            >
              <span aria-hidden>{item.icon}</span>
              {item.label}
              {badge}
            </Link>
          );
        })}
      </nav>
      <div className="border-t border-line p-2 space-y-1">
        <button
          onClick={() => {
            setShowNotif((s) => !s);
            if (!showNotif && (notif?.unread ?? 0) > 0) {
              void apiCall('/api/notifications', 'PATCH', { markAllRead: true }).then(() => refresh());
            }
          }}
          className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-xs text-ink-muted hover:bg-surface-sunken"
        >
          <span aria-hidden>🔔</span> Notifications
          {(notif?.unread ?? 0) > 0 && (
            <span className="ml-auto rounded-full bg-accent px-1.5 text-2xs font-semibold text-white">{notif?.unread}</span>
          )}
        </button>
        {showNotif && (
          <div className="max-h-64 overflow-y-auto rounded-md border border-line bg-surface p-1">
            {(notif?.notifications ?? []).length === 0 && <p className="p-2 text-2xs text-ink-faint">No notifications</p>}
            {(notif?.notifications ?? []).slice(0, 15).map((n) => (
              <div key={n.id} className="border-b border-line/50 p-2 last:border-0">
                <p className="text-2xs font-medium">{n.title}</p>
                <p className="text-2xs text-ink-faint">{n.type.replace(/_/g, ' ')}</p>
              </div>
            ))}
          </div>
        )}
        <button
          onClick={toggleTheme}
          className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-xs text-ink-muted hover:bg-surface-sunken"
        >
          <span aria-hidden>{dark ? '☀' : '☾'}</span> {dark ? 'Light mode' : 'Dark mode'}
        </button>
      </div>
    </aside>
  );
}
