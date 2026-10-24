import { useCallback, useEffect, useRef, useState } from 'react';

import { apiPost, errorMessage, isAbort } from '@/shared/api/client';
import type { GeneratedInsight, InsightRequest } from '@/shared/api/types';
import type { Role } from '@/shared/lib/demo';

type Params = {
  company: string;
  newsId: string;
  role: Role | null;
  snapshot: string | null;
  relatedCompanyId?: string;
};

type Run = { attempt: number; refresh: boolean };

type Result = { key: string; data?: GeneratedInsight; error?: string; cancelled?: boolean };

async function request(params: Params, refresh: boolean, signal: AbortSignal) {
  const body: InsightRequest = {
    news_ids: [params.newsId],
    role: params.role,
    feed_snapshot_id: params.snapshot,
    refresh,
    ...(params.relatedCompanyId ? { related_company_id: params.relatedCompanyId } : {}),
  };
  const path = `/companies/${encodeURIComponent(params.company)}/insights`;
  return apiPost<GeneratedInsight>(path, body, signal);
}

export function useInsight(params: Params) {
  const { company, newsId, role, snapshot, relatedCompanyId } = params;
  const [run, setRun] = useState<Run>({ attempt: 0, refresh: false });
  const [result, setResult] = useState<Result | null>(null);
  const controller = useRef<AbortController | null>(null);
  const key = `${company}|${newsId}|${role ?? ''}|${snapshot ?? ''}|${relatedCompanyId ?? ''}|${run.attempt}`;

  useEffect(() => {
    const current = new AbortController();
    controller.current = current;
    request(
      { company, newsId, role, snapshot, relatedCompanyId },
      run.refresh,
      current.signal,
    ).then(
      (data) => {
        if (!current.signal.aborted) setResult({ key, data });
      },
      (error: unknown) => {
        if (current.signal.aborted || isAbort(error)) return;
        setResult({ key, error: errorMessage(error) });
      },
    );
    return () => current.abort();
  }, [company, newsId, role, snapshot, relatedCompanyId, run, key]);

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
