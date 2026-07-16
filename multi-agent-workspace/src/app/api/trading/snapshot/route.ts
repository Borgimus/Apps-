import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

const ENDPOINTS = {
  health: '/health',
  status: '/status',
  account: '/account',
  positions: '/positions',
  orders: '/orders?limit=50',
  signals: '/signals?limit=50',
  risk: '/risk',
  pulse: '/api/session/pulse',
  pushEvents: '/api/session/push-events?limit=20',
} as const;

type EndpointName = keyof typeof ENDPOINTS;

function configuredBaseUrl(): URL {
  const value = process.env.TRADING_API_URL?.trim() || 'http://127.0.0.1:8000';
  const url = new URL(value);
  if (!['http:', 'https:'].includes(url.protocol)) {
    throw new Error('TRADING_API_URL must use http or https');
  }
  url.pathname = url.pathname.replace(/\/$/, '');
  return url;
}

async function readEndpoint(base: URL, path: string): Promise<unknown> {
  const url = new URL(path, base);
  const response = await fetch(url, {
    method: 'GET',
    cache: 'no-store',
    signal: AbortSignal.timeout(5_000),
    headers: { accept: 'application/json' },
  });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

function sanitizeAccount(value: unknown): unknown {
  if (!value || typeof value !== 'object') return value;
  const account = value as Record<string, unknown>;
  return {
    equity: account.equity,
    cash: account.cash,
    buying_power: account.buying_power,
    is_paper: account.is_paper,
  };
}

export async function GET() {
  let base: URL;
  try {
    base = configuredBaseUrl();
  } catch (error) {
    return NextResponse.json({ ok: false, error: String(error) }, { status: 500 });
  }

  const entries = await Promise.all(
    (Object.entries(ENDPOINTS) as Array<[EndpointName, string]>).map(async ([name, path]) => {
      try {
        const value = await readEndpoint(base, path);
        return [name, { value: name === 'account' ? sanitizeAccount(value) : value }] as const;
      } catch (error) {
        return [name, { value: null, error: error instanceof Error ? error.message : String(error) }] as const;
      }
    }),
  );

  const results = Object.fromEntries(entries) as Record<EndpointName, { value: unknown; error?: string }>;
  const errors = Object.fromEntries(
    entries.filter(([, result]) => result.error).map(([name, result]) => [name, result.error]),
  );

  return NextResponse.json(
    {
      ok: true,
      data: {
        connected: Boolean(results.health.value),
        fetchedAt: new Date().toISOString(),
        health: results.health.value,
        status: results.status.value,
        account: results.account.value,
        positions: results.positions.value ?? [],
        orders: results.orders.value ?? [],
        signals: results.signals.value ?? [],
        risk: results.risk.value,
        pulse: results.pulse.value,
        pushEvents: results.pushEvents.value ?? { events: [], total: 0 },
        errors,
      },
    },
    { headers: { 'cache-control': 'no-store' } },
  );
}
