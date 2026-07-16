'use client';

import { useMemo } from 'react';
import { useApi } from '@/components/hooks';
import { Badge, Card, EmptyState, Spinner, cls } from '@/components/ui';
import { cycleFreshness, formatTradingMoney, formatTradingTime, freshness, pnlClass } from '@/lib/trading-utils';

interface TradingStatus {
  live_trading_enabled: boolean;
  broker: string;
  kill_switch_active: boolean;
  session_date: string | null;
  trades_today: number;
  daily_pnl: number;
}

interface TradingAccount {
  equity: number;
  cash: number;
  buying_power: number;
  is_paper: boolean;
}

interface Position {
  symbol: string;
  option_symbol: string;
  quantity: number;
  avg_cost: number;
  current_price: number | null;
  unrealized_pnl: number | null;
  strategy_id: string | null;
  opened_at: string | null;
}

interface Order {
  order_id: string;
  symbol: string;
  option_symbol: string;
  side: string;
  quantity: number;
  limit_price: number;
  filled_price: number | null;
  status: string;
  is_paper: boolean;
  submitted_at: string | null;
}

interface Signal {
  id: number;
  strategy_id: string;
  symbol: string;
  direction: string;
  timestamp: string;
  price: number;
  confidence: number | null;
  notes: string | null;
}

interface RiskEvent {
  event_type: string;
  check_name: string | null;
  message: string;
  timestamp: string;
}

interface RiskSnapshot {
  trades_today: number;
  daily_pnl: number;
  kill_switch_active: boolean;
  max_trades_per_day: number;
  max_daily_loss_pct: number;
  recent_events: RiskEvent[];
}

interface SessionPulse {
  session_active: boolean;
  stale_secs: number;
  ts: string;
  session_date: string;
  cycle: number;
  positions: number;
  entries_today: number;
  pending_orders: number;
  daily_pnl: number;
  unrealized_pnl: number;
  net_pnl: number;
  active_symbols: string[];
  scanner_standby: boolean;
}

interface PushEvent {
  event: string;
  message: string;
  data: Record<string, unknown>;
  consumed: boolean;
}

interface PushEventsSnapshot {
  events: PushEvent[];
  total: number;
}

interface Snapshot {
  connected: boolean;
  fetchedAt: string;
  health: { status: string; timestamp: string } | null;
  status: TradingStatus | null;
  account: TradingAccount | null;
  positions: Position[];
  orders: Order[];
  signals: Signal[];
  risk: RiskSnapshot | null;
  pulse: SessionPulse | null;
  pushEvents: PushEventsSnapshot;
  errors: Record<string, string>;
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <Card>
      <p className="text-2xs uppercase tracking-wider text-ink-faint">{label}</p>
      <p className={cls('mt-1 text-xl font-semibold tabular-nums', tone)}>{value}</p>
    </Card>
  );
}

export default function TradingDashboardPage() {
  const { data, loading, error, refresh } = useApi<Snapshot>('/api/trading/snapshot', 5_000);
  const positionsUnrealized = useMemo(
    () => data?.positions.reduce((sum, position) => sum + (position.unrealized_pnl ?? 0), 0) ?? null,
    [data?.positions],
  );
  const unrealized = data?.pulse?.unrealized_pnl ?? positionsUnrealized;
  const netPnl = data?.pulse?.net_pnl ?? data?.status?.daily_pnl;
  const mode = data?.status
    ? data.status.live_trading_enabled
      ? 'LIVE'
      : 'PAPER'
    : data?.account?.is_paper
      ? 'PAPER'
      : 'UNKNOWN';
  const fresh = data?.pulse ? cycleFreshness(data.pulse.stale_secs) : freshness(data?.health?.timestamp ?? data?.fetchedAt);
  const connectionLabel = data?.connected ? fresh : 'disconnected';

  return (
    <div className="mx-auto max-w-[1500px] space-y-4 p-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-lg font-semibold">Options Trading Dashboard</h1>
            <Badge status={mode === 'LIVE' ? 'error' : 'active'} label={mode} />
            <Badge status={data?.connected ? (fresh === 'stale' ? 'pending' : 'healthy') : 'error'} label={connectionLabel} />
            <span className="rounded-full border border-line px-2 py-0.5 text-2xs text-ink-muted">READ ONLY</span>
          </div>
          <p className="mt-1 text-xs text-ink-muted">
            Broker: {data?.status?.broker ?? 'unavailable'} · Cycle: {data?.pulse?.cycle ?? '—'} · Last API response: {formatTradingTime(data?.fetchedAt)}
          </p>
        </div>
        <button className="rounded-md border border-line px-3 py-1.5 text-xs hover:bg-surface-sunken" onClick={() => void refresh()}>
          Refresh
        </button>
      </header>

      {loading && !data && <Spinner label="Connecting to the trading API…" />}
      {(error || !data?.connected) && (
        <div className="rounded-lg border border-rose-500/40 bg-rose-500/10 p-3 text-xs text-rose-500">
          Trading API disconnected. Confirm TRADING_API_URL is reachable from the Next.js server.
          {error ? ` ${error}` : ''}
        </div>
      )}
      {mode === 'LIVE' && (
        <div className="rounded-lg border border-rose-500 bg-rose-500/10 p-3 text-sm font-semibold text-rose-500">
          LIVE MODE REPORTED BY BACKEND — this page remains read-only.
        </div>
      )}
      {Object.keys(data?.errors ?? {}).length > 0 && (
        <details className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-xs">
          <summary className="cursor-pointer font-medium text-amber-600">Some data sources are unavailable</summary>
          <pre className="mt-2 whitespace-pre-wrap text-ink-muted">{JSON.stringify(data?.errors, null, 2)}</pre>
        </details>
      )}

      <section className="grid grid-cols-2 gap-3 lg:grid-cols-6">
        <Metric label="Equity" value={formatTradingMoney(data?.account?.equity)} />
        <Metric label="Buying power" value={formatTradingMoney(data?.account?.buying_power)} />
        <Metric label="Net P&L" value={formatTradingMoney(netPnl)} tone={pnlClass(netPnl)} />
        <Metric label="Unrealized P&L" value={formatTradingMoney(unrealized)} tone={pnlClass(unrealized)} />
        <Metric label="Open positions" value={String(data?.pulse?.positions ?? data?.positions.length ?? 0)} />
        <Metric label="Entries today" value={`${data?.pulse?.entries_today ?? data?.status?.trades_today ?? 0}/${data?.risk?.max_trades_per_day ?? '—'}`} />
      </section>

      <div className="rounded-lg border border-sky-500/30 bg-sky-500/10 p-3 text-xs text-sky-600 dark:text-sky-300">
        Live ticker motion and realized P&L are not exposed by the current Python API. This dashboard will not infer or fabricate them.
      </div>

      <section className="grid gap-4 xl:grid-cols-[1.2fr_0.8fr]">
        <Card>
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold">Selection rationale</h2>
            <span className="text-2xs text-ink-faint">Signals, rules and logged notes</span>
          </div>
          {data?.signals.length ? (
            <div className="max-h-[440px] space-y-2 overflow-y-auto pr-1">
              {data.signals.map((signal) => (
                <details key={signal.id} className="rounded-md border border-line bg-surface p-3" open={false}>
                  <summary className="cursor-pointer list-none">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-semibold">{signal.symbol}</span>
                      <Badge status={signal.direction.toLowerCase().includes('long') ? 'active' : 'pending'} label={signal.direction} />
                      <span className="text-xs text-ink-muted">{signal.strategy_id}</span>
                      <span className="ml-auto text-2xs text-ink-faint">{formatTradingTime(signal.timestamp)}</span>
                    </div>
                    <div className="mt-2 flex gap-4 text-xs text-ink-muted">
                      <span>Signal price: {formatTradingMoney(signal.price)}</span>
                      <span>Confidence: {signal.confidence === null ? 'Unavailable' : `${Math.round(signal.confidence * 100)}%`}</span>
                    </div>
                  </summary>
                  <div className="mt-3 border-t border-line pt-3 text-xs text-ink-muted">
                    {signal.notes || 'No rationale notes were logged by the strategy.'}
                  </div>
                </details>
              ))}
            </div>
          ) : (
            <EmptyState title="No signals recorded" hint="The trading engine currently evaluates the watchlist every five minutes." />
          )}
        </Card>

        <Card>
          <h2 className="mb-3 text-sm font-semibold">Risk and connection</h2>
          <dl className="grid grid-cols-2 gap-3 text-xs">
            <div><dt className="text-ink-faint">API health</dt><dd className="mt-1 font-medium">{data?.health?.status ?? 'Unavailable'}</dd></div>
            <div><dt className="text-ink-faint">Runner heartbeat</dt><dd className="mt-1 font-medium capitalize">{fresh}{data?.pulse ? ` (${data.pulse.stale_secs}s)` : ''}</dd></div>
            <div><dt className="text-ink-faint">Kill switch state</dt><dd className="mt-1 font-medium">{data?.status?.kill_switch_active ? 'Active' : 'Inactive'}</dd></div>
            <div><dt className="text-ink-faint">Daily loss limit</dt><dd className="mt-1 font-medium">{data?.risk ? `${(data.risk.max_daily_loss_pct * 100).toFixed(1)}%` : 'Unavailable'}</dd></div>
            <div><dt className="text-ink-faint">Scanner</dt><dd className="mt-1 font-medium">{data?.pulse?.scanner_standby ? 'Standby' : 'Active'}</dd></div>
            <div><dt className="text-ink-faint">Pending orders</dt><dd className="mt-1 font-medium">{data?.pulse?.pending_orders ?? 'Unavailable'}</dd></div>
          </dl>
          <div className="mt-4 border-t border-line pt-3">
            <p className="mb-2 text-xs font-medium">Recent risk events</p>
            <div className="max-h-72 space-y-2 overflow-y-auto">
              {(data?.risk?.recent_events ?? []).map((event, index) => (
                <div key={`${event.timestamp}-${index}`} className="rounded-md bg-surface-sunken p-2 text-xs">
                  <div className="flex justify-between gap-2"><span className="font-medium">{event.check_name || event.event_type}</span><span className="text-2xs text-ink-faint">{formatTradingTime(event.timestamp)}</span></div>
                  <p className="mt-1 text-ink-muted">{event.message}</p>
                </div>
              ))}
              {(data?.risk?.recent_events ?? []).length === 0 && <p className="text-xs text-ink-faint">No risk events recorded.</p>}
            </div>
          </div>
          <div className="mt-4 border-t border-line pt-3">
            <div className="mb-2 flex items-center justify-between">
              <p className="text-xs font-medium">Session activity</p>
              <span className="text-2xs text-ink-faint">{data?.pushEvents.total ?? 0} events</span>
            </div>
            <div className="max-h-72 space-y-2 overflow-y-auto">
              {(data?.pushEvents.events ?? []).map((event, index) => (
                <div key={`${event.event}-${index}`} className="rounded-md bg-surface-sunken p-2 text-xs">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium capitalize">{event.event.replace(/_/g, ' ')}</span>
                    <span className="text-2xs text-ink-faint">{formatTradingTime(typeof event.data.ts === 'string' ? event.data.ts : null)}</span>
                  </div>
                  <p className="mt-1 text-ink-muted">{event.message}</p>
                </div>
              ))}
              {(data?.pushEvents.events ?? []).length === 0 && <p className="text-xs text-ink-faint">No session events recorded.</p>}
            </div>
          </div>
        </Card>
      </section>

      <Card>
        <h2 className="mb-3 text-sm font-semibold">Open positions</h2>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="border-b border-line text-ink-faint"><tr><th className="pb-2">Symbol</th><th className="pb-2">Contract</th><th className="pb-2">Strategy</th><th className="pb-2 text-right">Qty</th><th className="pb-2 text-right">Avg cost</th><th className="pb-2 text-right">Current</th><th className="pb-2 text-right">Unrealized</th></tr></thead>
            <tbody>
              {(data?.positions ?? []).map((position) => (
                <tr key={position.option_symbol} className="border-b border-line/60 last:border-0">
                  <td className="py-2 font-medium">{position.symbol}</td><td className="py-2">{position.option_symbol}</td><td className="py-2 text-ink-muted">{position.strategy_id ?? '—'}</td><td className="py-2 text-right tabular-nums">{position.quantity}</td><td className="py-2 text-right tabular-nums">{formatTradingMoney(position.avg_cost)}</td><td className="py-2 text-right tabular-nums">{formatTradingMoney(position.current_price)}</td><td className={cls('py-2 text-right tabular-nums', pnlClass(position.unrealized_pnl))}>{formatTradingMoney(position.unrealized_pnl)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {(data?.positions ?? []).length === 0 && <p className="py-6 text-center text-xs text-ink-faint">No open positions.</p>}
        </div>
      </Card>

      <Card>
        <h2 className="mb-3 text-sm font-semibold">Recent orders</h2>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="border-b border-line text-ink-faint"><tr><th className="pb-2">Time</th><th className="pb-2">Symbol</th><th className="pb-2">Contract</th><th className="pb-2">Side</th><th className="pb-2 text-right">Qty</th><th className="pb-2 text-right">Limit</th><th className="pb-2 text-right">Fill</th><th className="pb-2">Status</th></tr></thead>
            <tbody>
              {(data?.orders ?? []).map((order) => (
                <tr key={order.order_id} className="border-b border-line/60 last:border-0">
                  <td className="py-2 text-ink-muted">{formatTradingTime(order.submitted_at)}</td><td className="py-2 font-medium">{order.symbol}</td><td className="py-2">{order.option_symbol}</td><td className="py-2">{order.side}</td><td className="py-2 text-right">{order.quantity}</td><td className="py-2 text-right">{formatTradingMoney(order.limit_price)}</td><td className="py-2 text-right">{formatTradingMoney(order.filled_price)}</td><td className="py-2"><Badge status={order.status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
          {(data?.orders ?? []).length === 0 && <p className="py-6 text-center text-xs text-ink-faint">No orders recorded.</p>}
        </div>
      </Card>
    </div>
  );
}
