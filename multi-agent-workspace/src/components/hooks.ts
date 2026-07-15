'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

interface ApiState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  refresh: () => Promise<void>;
}

/** Fetch a JSON API endpoint with optional polling. */
export function useApi<T>(url: string | null, pollMs?: number): ApiState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    if (!url) return;
    try {
      const res = await fetch(url, { cache: 'no-store' });
      const body = (await res.json()) as { ok: boolean; data?: T; error?: string };
      if (body.ok) {
        setData(body.data ?? null);
        setError(null);
      } else {
        setError(body.error ?? `Request failed (${res.status})`);
      }
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  }, [url]);

  useEffect(() => {
    setLoading(true);
    void refresh();
    if (pollMs) {
      const t = setInterval(() => void refresh(), pollMs);
      return () => clearInterval(t);
    }
  }, [refresh, pollMs]);

  return { data, error, loading, refresh };
}

export async function apiCall(
  url: string,
  method: 'POST' | 'PUT' | 'PATCH' | 'DELETE',
  body?: unknown,
): Promise<{ ok: boolean; data?: unknown; error?: string }> {
  try {
    const res = await fetch(url, {
      method,
      headers: { 'content-type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    return (await res.json()) as { ok: boolean; data?: unknown; error?: string };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

export interface LiveEvent {
  id: string;
  projectId: string | null;
  actor: string;
  type: string;
  summary: string;
  data: Record<string, unknown>;
  refId?: string | null;
  createdAt: string;
}

/** Subscribe to a project's live SSE activity stream. */
export function useLiveEvents(projectId: string | null, max = 400): LiveEvent[] {
  const [events, setEvents] = useState<LiveEvent[]>([]);
  const seen = useRef(new Set<string>());

  useEffect(() => {
    if (!projectId) return;
    seen.current = new Set();
    setEvents([]);
    const source = new EventSource(`/api/projects/${projectId}/events`);
    source.onmessage = (e) => {
      try {
        const event = JSON.parse(e.data) as LiveEvent;
        if (seen.current.has(event.id)) return;
        seen.current.add(event.id);
        setEvents((prev) => [...prev.slice(-(max - 1)), event]);
      } catch {
        /* ignore malformed frames */
      }
    };
    return () => source.close();
  }, [projectId, max]);

  return events;
}
