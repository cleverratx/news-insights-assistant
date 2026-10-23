import { useSyncExternalStore } from 'react';

import { apiGet } from '@/shared/api/client';
import { useLoader, type ApiState } from '@/shared/api/useApi';

const TTL_MS = 5 * 60 * 1000;

const entries = new Map<string, { at: number; promise: Promise<unknown> }>();
const listeners = new Set<() => void>();
let version = 0;

function cachedGet<T>(path: string): Promise<T> {
  const hit = entries.get(path);
  if (hit && Date.now() - hit.at < TTL_MS) return hit.promise as Promise<T>;
  const promise = apiGet<T>(path);
  entries.set(path, { at: Date.now(), promise });
  promise.catch(() => entries.delete(path));
  return promise;
}

async function loadCached<T>(key: string, signal: AbortSignal): Promise<T> {
  const data = await cachedGet<T>(key.slice(key.indexOf('|') + 1));
  signal.throwIfAborted();
  return data;
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function invalidateCached(prefix: string) {
  [...entries.keys()].filter((key) => key.startsWith(prefix)).forEach((key) => entries.delete(key));
  version += 1;
  listeners.forEach((listener) => listener());
}

export function useCachedApi<T>(path: string | null): ApiState<T> {
  const current = useSyncExternalStore(subscribe, () => version);
  return useLoader<T>(path === null ? null : `${current}|${path}`, loadCached);
}
