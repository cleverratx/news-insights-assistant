import { useState } from 'react';

import type { AgentTrace } from '@/shared/api/types';
import { useApi } from '@/shared/api/useApi';
import { ErrorState, Skeleton } from '@/shared/ui/StateView';

import styles from './TraceSteps.module.css';

const STATUS = { ok: 'выполнен', error: 'ошибка', timeout: 'не уложился во время' } as const;

export function TraceSteps({ insightId }: { insightId: string }) {
  const [open, setOpen] = useState(false);
  const trace = useApi<AgentTrace>(
    open ? `/insights/${encodeURIComponent(insightId)}/trace` : null,
  );

  return (
    <details className={styles.details} onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary className={styles.summary}>Как агент собирал разбор</summary>
      {trace.loading && <Skeleton lines={3} />}
      {trace.error && (
        <ErrorState title="Не удалось загрузить шаги" message={trace.error} onRetry={trace.retry} />
      )}
      {trace.data && (
        <ol className={styles.steps}>
          {(trace.data.steps ?? []).map((step) => (
            <li key={step.number}>
              {step.goal}
              <span className={styles.meta}>
                {' · '}
                {STATUS[step.status]}
                {step.reused_from ? ' · взято из кеша' : ''}
              </span>
            </li>
          ))}
        </ol>
      )}
    </details>
  );
}
