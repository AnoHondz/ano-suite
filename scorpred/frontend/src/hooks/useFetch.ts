import { useCallback, useEffect, useState, useSyncExternalStore } from 'react';

interface FetchState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  status?: number;
}

const FETCH_TIMEOUT_MS = 45_000;
const CACHE_TTL_MS = 15_000;
const cache = new Map<string, { data: unknown; expires: number }>();
const flights = new Map<string, Promise<unknown>>();
const refreshListeners = new Set<() => void>();
const activityListeners = new Set<() => void>();
const notifyActivity = () => activityListeners.forEach(listener => listener());
const subscribeActivity = (listener: () => void) => {
  activityListeners.add(listener);
  return () => { activityListeners.delete(listener); };
};

function keyFor(url: string) {
  const parsed = new URL(url, window.location.origin);
  for (const [name, value] of [...parsed.searchParams]) if (!value) parsed.searchParams.delete(name);
  parsed.searchParams.sort();
  return parsed.pathname + parsed.search;
}

function cached(key: string) {
  const entry = cache.get(key);
  if (entry && entry.expires > Date.now()) return entry;
  cache.delete(key);
}

function sourceDeadline(value: unknown): number {
  if (!value || typeof value !== 'object') return Infinity;
  if (Array.isArray(value)) return Math.min(Infinity, ...value.map(sourceDeadline));
  const record = value as Record<string, unknown>;
  const expires = typeof record.expires_at === 'string' ? Date.parse(record.expires_at) : Infinity;
  return Math.min(Number.isFinite(expires) ? expires : Infinity, ...Object.values(record).map(sourceDeadline));
}

function request(url: string, key: string) {
  const existing = flights.get(key);
  if (existing) return existing;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  const promise = fetch(url, { signal: controller.signal, cache: 'no-store' }).then(async res => {
    if (!res.headers.get('content-type')?.includes('application/json')) throw new Error('The server returned an unexpected response.');
    const payload = await res.json();
    if (!res.ok) throw Object.assign(new Error(payload.error || payload.message || (res.status === 503 ? 'Data is temporarily unavailable. Please try again.' : `Server returned ${res.status}`)), { status: res.status });
    // Session/CSRF responses are never retained. Source timestamps bound every cache hit.
    if (key !== '/api/session') {
      if (cache.size >= 100) cache.delete(cache.keys().next().value!);
      cache.set(key, { data: payload, expires: Math.min(Date.now() + CACHE_TTL_MS, sourceDeadline(payload)) });
    }
    return payload;
  }).catch((error: Error) => {
    if (error.name === 'AbortError') throw new Error('The request timed out. Please try again.');
    throw error;
  }).finally(() => {
    clearTimeout(timer);
    flights.delete(key);
    notifyActivity();
  });
  flights.set(key, promise);
  notifyActivity();
  return promise;
}

/** Refresh the view; the server retains the provider's actual observation times. */
export function refreshData() {
  cache.clear();
  refreshListeners.forEach(listener => listener());
}

export function useFetchActivity() {
  return useSyncExternalStore(subscribeActivity, () => flights.size);
}

export function useFetch<T>(url: string | null): FetchState<T> & { retry: () => void } {
  const key = url ? keyFor(url) : '';
  const [state, setState] = useState<FetchState<T> & { key: string }>(() => {
    const entry = key ? cached(key) : undefined;
    return { key, data: entry?.data as T ?? null, loading: Boolean(url && !entry), error: null };
  });
  const [revision, setRevision] = useState(0);
  const retry = useCallback(() => {
    cache.delete(key);
    setRevision(value => value + 1);
  }, [key]);

  useEffect(() => {
    const refresh = () => setRevision(value => value + 1);
    refreshListeners.add(refresh);
    return () => { refreshListeners.delete(refresh); };
  }, []);

  useEffect(() => {
    if (!url) return;
    let active = true;
    const entry = cached(key);
    queueMicrotask(() => {
      if (active) setState({ key, data: entry?.data as T ?? null, loading: !entry, error: null });
    });
    if (!entry) request(url, key).then(data => {
      if (active) setState({ key, data: data as T, loading: false, error: null });
    }).catch((error: Error & { status?: number }) => {
      if (active) setState({ key, data: null, loading: false, error: error.message, status: error.status });
    });
    // Shared requests own their timeout; navigation cannot cancel another subscriber.
    return () => { active = false; };
  }, [url, key, revision]);

  if (!url) return { data: null, loading: false, error: null, retry };
  const entry = cached(key);
  return { ...(state.key === key ? state : { data: entry?.data as T ?? null, loading: !entry, error: null }), retry };
}
