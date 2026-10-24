import { useSyncExternalStore } from 'react';

import { DEMO_USERS, DEMO_COMPANIES, isRole, type DemoUser } from '@/shared/lib/demo';
import { clearFeedNavigation } from '@/features/feed/navigation';

const KEY = 'alfa-demo-login';
const CUSTOM_KEY = 'alfa-position-user';
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
let customUser: DemoUser | null = readCustom();

function readCustom(): DemoUser | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(CUSTOM_KEY) ?? 'null');
    return value &&
      typeof value.name === 'string' &&
      typeof value.title === 'string' &&
      typeof value.login === 'string' &&
      isRole(value.role) &&
      DEMO_COMPANIES.includes(value.company) &&
      typeof value.position === 'string' &&
      /^position:[0-9a-f]{32}$/.test(value.position)
      ? value
      : null;
  } catch {
    return null;
  }
}

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
  return (
    (customUser?.login === login ? customUser : null) ??
    DEMO_USERS.find((user) => user.login === login) ??
    null
  );
}

export function useSession(): DemoUser | null {
  return useSyncExternalStore(subscribe, snapshot);
}

export function signIn(value: string) {
  change(value);
}

export function savedProfile(): DemoUser | null {
  return customUser;
}

export function signInProfile(user: DemoUser) {
  customUser = user;
  try {
    sessionStorage.setItem(CUSTOM_KEY, JSON.stringify(user));
  } catch {
    change(user.login);
    return;
  }
  change(user.login);
}

export function signOut() {
  clearFeedNavigation();
  change(null);
}
