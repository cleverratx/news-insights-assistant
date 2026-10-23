import { useId, useState } from 'react';

import type { AgentTrace } from '@/shared/api/types';
import { useApi } from '@/shared/api/useApi';
import { plural } from '@/shared/lib/format';
import { ErrorState, Skeleton } from '@/shared/ui/StateView';

import styles from './TraceSteps.module.css';

type Forms = { one: string; few: string; many: string };

function counted(count: number, forms: Forms): string {
  return `${count}\u00a0${plural(count, forms)}`;
}

type Step = NonNullable<AgentTrace['steps']>[number];
type Check = NonNullable<AgentTrace['checks']>[number];

const TOOL_STATUS = {
  ok: 'выполнен',
  error: 'источник не ответил',
  timeout: 'не уложился во время',
} as const;

export function TraceSteps({ insightId }: { insightId: string }) {
  const [open, setOpen] = useState(false);
  const trace = useApi<AgentTrace>(
    open ? `/insights/${encodeURIComponent(insightId)}/trace` : null,
  );
  const checks = trace.data?.checks ?? [];

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
              <StepRow
                step={step}
                check={step.check_index != null ? checks[step.check_index] : undefined}
              />
            </li>
          ))}
        </ol>
      )}
    </details>
  );
}

function StepRow({ step, check }: { step: Step; check?: Check }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const reused = step.reused_from ? ' · взято из кеша' : '';

  if (step.action !== 'finish' || !check) {
    const failed = step.status !== 'ok';
    return (
      <>
        {step.goal}
        <span className={failed ? styles.warn : styles.meta}>
          {' · '}
          {TOOL_STATUS[step.status]}
          {reused}
        </span>
      </>
    );
  }

  const issues = check.issues ?? [];
  if (check.passed || issues.length === 0) {
    return (
      <>
        {step.goal}
        <span className={styles.meta}> · проверка пройдена{reused}</span>
      </>
    );
  }

  return (
    <>
      {step.goal}
      <span className={styles.meta}> · </span>
      <button
        type="button"
        className={styles.revise}
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((value) => !value)}
      >
        проверка вернула на доработку:{' '}
        {counted(issues.length, { one: 'замечание', few: 'замечания', many: 'замечаний' })}
      </button>
      {open && (
        <ul id={id} className={styles.issues}>
          {issues.map((issue, index) => (
            <li key={`${issue.statement_id ?? 'all'}-${index}`}>{issue.detail}</li>
          ))}
        </ul>
      )}
    </>
  );
}
