export type Freshness = 'fresh' | 'delayed' | 'stale' | 'unknown';

export function formatTradingMoney(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return 'Unavailable';
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

export function freshness(timestamp: string | null | undefined, now = Date.now()): Freshness {
  if (!timestamp) return 'unknown';
  const parsed = Date.parse(timestamp);
  if (!Number.isFinite(parsed)) return 'unknown';
  const age = Math.max(0, now - parsed);
  if (age <= 15_000) return 'fresh';
  if (age <= 60_000) return 'delayed';
  return 'stale';
}

export function formatTradingTime(timestamp: string | null | undefined): string {
  if (!timestamp) return 'Unavailable';
  const parsed = new Date(timestamp);
  if (Number.isNaN(parsed.getTime())) return 'Unavailable';
  return parsed.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export function pnlClass(value: number | null | undefined): string {
  if (value === null || value === undefined || value === 0) return 'text-ink';
  return value > 0 ? 'text-emerald-500' : 'text-rose-500';
}
