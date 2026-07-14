'use client';

import { ReactNode, useEffect } from 'react';

export function cls(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(' ');
}

// ---------------------------------------------------------------------------
// Status colors — one consistent language across the whole app
// ---------------------------------------------------------------------------
const STATUS_STYLES: Record<string, string> = {
  green: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/50 dark:text-emerald-300',
  blue: 'bg-sky-100 text-sky-800 dark:bg-sky-900/50 dark:text-sky-300',
  amber: 'bg-amber-100 text-amber-800 dark:bg-amber-900/50 dark:text-amber-300',
  red: 'bg-rose-100 text-rose-800 dark:bg-rose-900/50 dark:text-rose-300',
  gray: 'bg-surface-sunken text-ink-muted',
  violet: 'bg-violet-100 text-violet-800 dark:bg-violet-900/50 dark:text-violet-300',
};

export function statusColor(status: string): keyof typeof STATUS_STYLES {
  const s = status.toLowerCase();
  if (['completed', 'ok', 'approved', 'active', 'healthy', 'idle', 'answer'].includes(s)) return 'green';
  if (['running', 'in_progress', 'working', 'queued'].includes(s)) return 'blue';
  if (['paused', 'awaiting_review', 'awaiting_approval', 'pending', 'interrupted', 'blocked', 'review_request', 'question'].includes(s)) return 'amber';
  if (['failed', 'error', 'cancelled', 'rejected', 'denied', 'objection', 'blocker'].includes(s)) return 'red';
  if (['review_result', 'decision', 'decision_proposal', 'handoff'].includes(s)) return 'violet';
  return 'gray';
}

export function Badge({ status, label }: { status: string; label?: string }) {
  return (
    <span
      className={cls(
        'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-2xs font-medium whitespace-nowrap',
        STATUS_STYLES[statusColor(status)],
      )}
    >
      {['running', 'working', 'in_progress'].includes(status.toLowerCase()) && (
        <span className="h-1.5 w-1.5 rounded-full bg-current animate-pulse" />
      )}
      {label ?? status.replace(/_/g, ' ')}
    </span>
  );
}

export function Card({ children, className, onClick }: { children: ReactNode; className?: string; onClick?: () => void }) {
  return (
    <div
      onClick={onClick}
      className={cls(
        'rounded-lg border border-line bg-surface-raised p-4',
        onClick && 'cursor-pointer transition-colors hover:border-accent/60',
        className,
      )}
    >
      {children}
    </div>
  );
}

export function Button({
  children,
  onClick,
  variant = 'default',
  disabled,
  type = 'button',
  className,
  title,
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: 'default' | 'primary' | 'danger' | 'ghost';
  disabled?: boolean;
  type?: 'button' | 'submit';
  className?: string;
  title?: string;
}) {
  const styles = {
    default: 'border border-line bg-surface-raised hover:bg-surface-sunken text-ink',
    primary: 'bg-accent text-white hover:opacity-90',
    danger: 'border border-rose-300 dark:border-rose-800 text-rose-700 dark:text-rose-300 hover:bg-rose-50 dark:hover:bg-rose-950',
    ghost: 'text-ink-muted hover:text-ink hover:bg-surface-sunken',
  }[variant];
  return (
    <button
      type={type}
      title={title}
      disabled={disabled}
      onClick={onClick}
      className={cls(
        'rounded-md px-3 py-1.5 text-xs font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed',
        styles,
        className,
      )}
    >
      {children}
    </button>
  );
}

export function Modal({
  open,
  onClose,
  title,
  children,
  wide,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  wide?: boolean;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 p-4 overflow-y-auto" onClick={onClose}>
      <div
        className={cls('mt-8 w-full rounded-lg border border-line bg-surface-raised shadow-xl', wide ? 'max-w-5xl' : 'max-w-xl')}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-line px-4 py-3">
          <h2 className="text-sm font-semibold">{title}</h2>
          <button onClick={onClose} className="text-ink-faint hover:text-ink text-lg leading-none" aria-label="Close">
            ×
          </button>
        </div>
        <div className="p-4">{children}</div>
      </div>
    </div>
  );
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-dashed border-line p-8 text-center">
      <p className="text-sm font-medium text-ink-muted">{title}</p>
      {hint && <p className="mt-1 text-xs text-ink-faint">{hint}</p>}
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 p-4 text-xs text-ink-muted">
      <span className="h-3 w-3 animate-spin rounded-full border-2 border-line border-t-accent" />
      {label ?? 'Loading…'}
    </div>
  );
}

export function JsonBlock({ value }: { value: unknown }) {
  const text = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  return <pre className="code-block">{text}</pre>;
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block text-xs">
      <span className="mb-1 block font-medium text-ink-muted">{label}</span>
      {children}
    </label>
  );
}

export const inputCls =
  'w-full rounded-md border border-line bg-surface px-2.5 py-1.5 text-xs text-ink outline-none focus:border-accent';

export function timeAgo(iso: string | Date): string {
  const d = typeof iso === 'string' ? new Date(iso) : iso;
  const secs = Math.max(0, (Date.now() - d.getTime()) / 1000);
  if (secs < 60) return `${Math.floor(secs)}s ago`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return d.toLocaleDateString();
}

export function usd(n: number): string {
  return n >= 0.01 || n === 0 ? `$${n.toFixed(2)}` : `$${n.toFixed(4)}`;
}
