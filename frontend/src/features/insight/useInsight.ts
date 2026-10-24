import { useCallback, useEffect, useRef, useState } from 'react';

import { ApiError, apiPost, errorMessage, isAbort } from '@/shared/api/client';
import type { GeneratedInsight, InsightRequest } from '@/shared/api/types';
import type { Role } from '@/shared/lib/demo';

type Params = {
  company: string;
  newsId: string;
  role: Role | null;
  snapshot: string | null;
};

type Run = { attempt: number; refresh: boolean };

type Result = { key: string; data?: GeneratedInsight; error?: string; cancelled?: boolean };

async function request(params: Params, refresh: boolean, signal: AbortSignal) {
  const body: InsightRequest = {
    news_ids: [params.newsId],
    role: params.role,
    feed_snapshot_id: params.snapshot,
    refresh,
  };
  const path = `/companies/${encodeURIComponent(params.company)}/insights`;
  try {
    return await apiPost<GeneratedInsight>(path, body, signal);
  } catch (error) {
    if (error instanceof ApiError && error.status === 409 && params.snapshot) {
      return apiPost<GeneratedInsight>(path, { ...body, feed_snapshot_id: null }, signal);
    }
    throw error;
  }
}

export function useInsight(params: Params) {
  const { company, newsId, role, snapshot } = params;
  const [run, setRun] = useState<Run>({ attempt: 0, refresh: false });
  const [result, setResult] = useState<Result | null>(null);
  const controller = useRef<AbortController | null>(null);
  const key = `${company}|${newsId}|${role ?? ''}|${snapshot ?? ''}|${run.attempt}`;

  useEffect(() => {
    const current = new AbortController();
    controller.current = current;
    request({ company, newsId, role, snapshot }, run.refresh, current.signal).then(
      (data) => {
        if (!current.signal.aborted) setResult({ key, data });
      },
      (error: unknown) => {
        if (current.signal.aborted || isAbort(error)) return;
        setResult({ key, error: errorMessage(error) });
      },
    );
    return () => current.abort();
  }, [company, newsId, role, snapshot, run, key]);

  const cancel = useCallback(() => {
    controller.current?.abort();
    setResult({ key, cancelled: true });
  }, [key]);

  const retry = useCallback(
    () => setRun((value) => ({ attempt: value.attempt + 1, refresh: false })),
    [],
  );
  const rebuild = useCallback(
    () => setRun((value) => ({ attempt: value.attempt + 1, refresh: true })),
    [],
  );

  const current = result?.key === key ? result : null;
  return {
    key,
    data: current?.data,
    error: current?.error,
    cancelled: current?.cancelled ?? false,
    loading: current === null,
    retry,
    rebuild,
    cancel,
  };
}
