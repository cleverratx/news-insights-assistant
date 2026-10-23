import type { components } from '@/shared/api/schema';
import type { GeneratedInsight } from '@/shared/api/types';

import styles from './StatusNotice.module.css';

type ExecutionStatus = components['schemas']['ExecutionStatus'];

const EXECUTION: Record<Exclude<ExecutionStatus, 'completed'>, string> = {
  step_limit: 'агент не уложился в лимит шагов',
  timeout: 'разбор не уложился во время',
  planner_error: 'агент не смог спланировать разбор',
  check_failed: 'часть выводов не прошла самопроверку и скрыта',
  unavailable: 'модель сейчас недоступна',
};

export function StatusNotice({ result }: { result: GeneratedInsight }) {
  const { insight, execution_status: execution } = result;
  const gaps = [...(result.gaps ?? [])].sort((a, b) => Number(b.blocking) - Number(a.blocking));

  if (insight.status === 'no_significant_link') {
    return (
      <div className={`${styles.notice} ${styles.calm}`} role="status">
        <p className={styles.title}>Это событие не затрагивает вашу компанию заметно</p>
        {insight.status_reason && <p>{insight.status_reason}</p>}
      </div>
    );
  }

  if (insight.status === 'insufficient_data') {
    return (
      <div className={`${styles.notice} ${styles.warn}`} role="status">
        <p className={styles.title}>Для уверенного вывода не хватает данных</p>
        <p>
          Показываем только то, что подтверждено источником
          {execution !== 'completed' ? `: ${EXECUTION[execution]}` : ''}.
        </p>
        {gaps.length > 0 && (
          <ul className={styles.gaps}>
            {gaps.map((gap) => (
              <li key={`${gap.kind}:${gap.missing}`} className={styles.gap}>
                <p className={styles.gapTitle}>{gap.missing}</p>
                <p>{gap.consequence}</p>
                {gap.remedy && <p className={styles.remedy}>Что можно сделать: {gap.remedy}</p>}
              </li>
            ))}
          </ul>
        )}
      </div>
    );
  }

  if (execution !== 'completed') {
    return (
      <div className={`${styles.notice} ${styles.warn}`} role="status">
        <p className={styles.title}>Разбор неполный</p>
        <p>Показываем то, что удалось подтвердить: {EXECUTION[execution]}.</p>
      </div>
    );
  }

  return null;
}
