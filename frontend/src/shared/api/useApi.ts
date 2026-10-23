import { useCallback, useEffect, useState } from 'react';

import { apiGet, errorMessage, isAbort } from '@/shared/api/client';

type Loader<T> = (key: string, signal: AbortSignal) => Promise<T>;

type Snapshot<T> = { key: string | null; data?: T; error?: string };

export type ApiState<T> = {
  data?: T;
  error?: string;
  loading: boolean;
  retry: () => void;
};

export function useLoader<T>(key: string | null, load: Loader<T>): ApiState<T> {
  const [snapshot, setSnapshot] = useState<Snapshot<T>>({ key: null });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (key === null) return;
    const controller = new AbortController();
    load(key, controller.signal).then(
      (data) => setSnapshot({ key, data }),
      (error: unknown) => {
        if (!isAbort(error)) setSnapshot({ key, error: errorMessage(error) });
      },
    );
    return () => controller.abort();
  }, [key, load, attempt]);

  const retry = useCallback(() => {
    setSnapshot({ key: null });
    setAttempt((value) => value + 1);
  }, []);

  const current = snapshot.key === key ? snapshot : { key };
  return {
    data: current.data,
    error: current.error,
    loading: key !== null && current.data === undefined && current.error === undefined,
    retry,
  };
}

export function useApi<T>(path: string | null): ApiState<T> {
  return useLoader<T>(path, apiGet);
}
