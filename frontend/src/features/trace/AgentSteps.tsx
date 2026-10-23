import { useId, useState } from 'react';

import type { AgentTrace } from '@/shared/api/types';
import { plural } from '@/shared/lib/format';

import styles from './BasisPanel.module.css';

type Step = NonNullable<AgentTrace['steps']>[number];
type Check = NonNullable<AgentTrace['checks']>[number];
type Rejection = NonNullable<AgentTrace['rejected']>[number];

const TOOLS: Record<string, string> = {
  search_bank_analytics: 'Поиск в аналитике Альфа-Банка',
  search_news: 'Поиск новостей',
  get_original: 'Чтение оригинала',
  get_profile: 'Данные компании',
  get_counterparty_graph: 'Связи с контрагентами',
};

const TOOL_STATUS = {
  ok: 'выполнен',
  error: 'источник не ответил',
  timeout: 'не уложился во время',
} as const;

const SECTIONS: Record<Rejection['section'], string> = {
  headline: 'Заголовок',
  what_happened: 'Что произошло',
  why_it_matters: 'Почему важно',
  risks: 'Риски',
  opportunities: 'Возможности',
  actions: 'Что сделать',
  bank_view: 'Позиция банка',
};

const seconds = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 });

const NBSP = String.fromCharCode(160);

function counted(count: number, forms: { one: string; few: string; many: string }): string {
  return `${count}${NBSP}${plural(count, forms)}`;
}

export function AgentSteps({ trace }: { trace: AgentTrace }) {
  const checks = trace.checks ?? [];
  const rejected = (trace.rejected ?? []).filter((item) => !item.shown);

  return (
    <>
      <ol className={styles.steps}>
        {(trace.steps ?? []).map((step) => (
          <li key={step.number} className={styles.step}>
            <StepRow
              step={step}
              check={step.check_index != null ? checks[step.check_index] : undefined}
            />
          </li>
        ))}
      </ol>
      {rejected.length > 0 && <Rejected items={rejected} />}
    </>
  );
}

function stepLabel(step: Step): string {
  if (step.action === 'finish') return 'Черновик вывода';
  return (step.tool && TOOLS[step.tool]) || step.tool || 'Шаг';
}

function StepRow({ step, check }: { step: Step; check?: Check }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const time = step.planning_seconds + step.elapsed_seconds;
  const issues = check?.issues ?? [];
  const returned = check != null && !check.passed && issues.length > 0;

  return (
    <>
      <p className={styles.stepHead}>
        <span className={styles.stepTool}>{stepLabel(step)}</span>
        <span className={styles.stepTime}>{seconds.format(time)}&nbsp;с</span>
      </p>
      <p className={styles.stepGoal}>{step.goal}</p>
      <p className={styles.stepStatus}>
        {step.reused_from && <span className={styles.muted}>взято из кеша · </span>}
        {step.action !== 'finish' || !check ? (
          <span className={step.status === 'ok' ? styles.muted : styles.warn}>
            {TOOL_STATUS[step.status]}
          </span>
        ) : returned ? (
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
        ) : (
          <span className={styles.ok}>проверка пройдена</span>
        )}
      </p>
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

function Rejected({ items }: { items: Rejection[] }) {
  const [open, setOpen] = useState(false);
  const id = useId();

  return (
    <div className={styles.rejected}>
      <button
        type="button"
        className={styles.more}
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((value) => !value)}
      >
        Проверка не пропустила в разбор{' '}
        {counted(items.length, { one: 'утверждение', few: 'утверждения', many: 'утверждений' })}
      </button>
      {open && (
        <ul id={id} className={styles.rejectedList}>
          {items.map((item) => (
            <li key={`${item.check_index}-${item.statement_id}`}>
              <p className={styles.rejectedMeta}>{SECTIONS[item.section]}</p>
              <p className={styles.rejectedText}>{item.text}</p>
              <p className={styles.rejectedWhy}>{item.problems.join(' ')}</p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
