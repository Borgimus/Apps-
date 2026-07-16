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

export function cycleFreshness(staleSeconds: number | null | undefined): Freshness {
  if (staleSeconds === null || staleSeconds === undefined || !Number.isFinite(staleSeconds)) return 'unknown';
  if (staleSeconds <= 330) return 'fresh';
  if (staleSeconds <= 660) return 'delayed';
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


export interface PositionMetricInput {
  unrealized_pnl: number | null | undefined;
}

export interface PulsePositionMetricInput {
  positions: number;
  unrealized_pnl: number;
}

export interface ReconciledPositionMetrics {
  count: number | null;
  unrealized: number | null;
  source: 'current' | 'cycle' | 'unavailable';
  drift: boolean;
  cycleCount: number | null;
  cycleUnrealized: number | null;
}

/**
 * The live positions endpoint is authoritative for the current position state.
 * The pulse is a five-minute cycle snapshot and is only a fallback when the
 * live endpoint is unavailable. Drift is surfaced instead of silently mixing
 * values captured at different moments.
 */
export function reconcilePositionMetrics(
  positions: PositionMetricInput[],
  pulse: PulsePositionMetricInput | null | undefined,
  positionsAvailable: boolean,
): ReconciledPositionMetrics {
  const cycleCount = pulse?.positions ?? null;
  const cycleUnrealized = pulse?.unrealized_pnl ?? null;

  if (!positionsAvailable) {
    return {
      count: cycleCount,
      unrealized: cycleUnrealized,
      source: pulse ? 'cycle' : 'unavailable',
      drift: false,
      cycleCount,
      cycleUnrealized,
    };
  }

  const values = positions.map((position) => position.unrealized_pnl);
  const markedValues = values.filter(
    (value): value is number => typeof value === 'number' && Number.isFinite(value),
  );
  const allMarked = markedValues.length === values.length;
  const unrealized = allMarked
    ? markedValues.reduce((sum, value) => sum + value, 0)
    : null;
  const count = positions.length;
  const drift = Boolean(
    pulse &&
      (cycleCount !== count ||
        (unrealized !== null &&
          cycleUnrealized !== null &&
          Math.abs(cycleUnrealized - unrealized) > 0.005)),
  );

  return {
    count,
    unrealized,
    source: 'current',
    drift,
    cycleCount,
    cycleUnrealized,
  };
}
