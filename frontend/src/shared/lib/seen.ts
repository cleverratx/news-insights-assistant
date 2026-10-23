import { useSyncExternalStore } from 'react';

const PREFIX = 'alfa-seen:';
const EMPTY: ReadonlySet<string> = new Set();
const snapshots = new Map<string, ReadonlySet<string>>();
const listeners = new Set<() => void>();

function storageKey(login: string, company: string): string {
  return `${PREFIX}${login}:${company}`;
}

function read(key: string): ReadonlySet<string> {
  const cached = snapshots.get(key);
  if (cached) return cached;
  let ids: ReadonlySet<string> = EMPTY;
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(key) ?? '[]');
    if (Array.isArray(parsed)) ids = new Set(parsed.filter((id) => typeof id === 'string'));
  } catch {
    ids = EMPTY;
  }
  snapshots.set(key, ids);
  return ids;
}

function attempt(action: () => void) {
  try {
    action();
  } catch {
    return;
  }
}

function notify() {
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function useSeen(login: string, company: string): ReadonlySet<string> {
  const key = storageKey(login, company);
  return useSyncExternalStore(subscribe, () => read(key));
}

export function markSeen(login: string, company: string, ids: string[]) {
  const key = storageKey(login, company);
  const current = read(key);
  if (ids.every((id) => current.has(id))) return;
  const next = new Set([...current, ...ids]);
  snapshots.set(key, next);
  attempt(() => localStorage.setItem(key, JSON.stringify([...next])));
  notify();
}

export function clearSeen() {
  snapshots.clear();
  attempt(() =>
    Object.keys(localStorage)
      .filter((key) => key.startsWith(PREFIX))
      .forEach((key) => localStorage.removeItem(key)),
  );
  notify();
}
