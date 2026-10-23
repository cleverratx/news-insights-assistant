import { useSyncExternalStore } from 'react';

import { DEMO_USERS, type DemoUser } from '@/shared/lib/demo';

const KEY = 'alfa-demo-login';
const listeners = new Set<() => void>();

function readStored(): string | null {
  try {
    return sessionStorage.getItem(KEY);
  } catch {
    return null;
  }
}

function persist(value: string | null) {
  try {
    if (value) sessionStorage.setItem(KEY, value);
    else sessionStorage.removeItem(KEY);
  } catch {
    return;
  }
}

let login = readStored();

function change(value: string | null) {
  login = value;
  persist(value);
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function snapshot(): DemoUser | null {
  return DEMO_USERS.find((user) => user.login === login) ?? null;
}

export function useSession(): DemoUser | null {
  return useSyncExternalStore(subscribe, snapshot);
}

export function signIn(value: string) {
  change(value);
}

export function signOut() {
  change(null);
}
