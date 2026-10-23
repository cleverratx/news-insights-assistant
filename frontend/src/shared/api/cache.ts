import { apiGet } from '@/shared/api/client';
import { useLoader, type ApiState } from '@/shared/api/useApi';

const TTL_MS = 5 * 60 * 1000;

const entries = new Map<string, { at: number; promise: Promise<unknown> }>();

function cachedGet<T>(path: string): Promise<T> {
  const hit = entries.get(path);
  if (hit && Date.now() - hit.at < TTL_MS) return hit.promise as Promise<T>;
  const promise = apiGet<T>(path);
  entries.set(path, { at: Date.now(), promise });
  promise.catch(() => entries.delete(path));
  return promise;
}

async function loadCached<T>(path: string, signal: AbortSignal): Promise<T> {
  const data = await cachedGet<T>(path);
  signal.throwIfAborted();
  return data;
}

export function useCachedApi<T>(path: string | null): ApiState<T> {
  return useLoader<T>(path, loadCached);
}
