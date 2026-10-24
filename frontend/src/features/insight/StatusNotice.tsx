import { ButtonDesktop } from '@alfalab/core-components-button/desktop';

import type { components } from '@/shared/api/schema';
import type { GeneratedInsight } from '@/shared/api/types';
import { plural } from '@/shared/lib/format';
import { EmptyState } from '@/shared/ui/StateView';

import { hasInsightContent, insightResultState } from './result-state';

import styles from './StatusNotice.module.css';

type ExecutionStatus = components['schemas']['ExecutionStatus'];

const EXECUTION: Record<Exclude<ExecutionStatus, 'completed'>, string> = {
  step_limit: 'Не успели завершить все проверки.',
  timeout: 'Подготовка полного разбора заняла слишком много времени.',
  planner_error: 'Не удалось завершить анализ материалов.',
  check_failed: 'Некоторые выводы не прошли проверку по источникам и скрыты.',
  unavailable: 'Сервис ИИ сейчас недоступен.',
};

export function StatusNotice({
  result,
  onRetry,
}: {
  result: GeneratedInsight;
  onRetry?: () => void;
}) {
  const { insight, execution_status: execution } = result;
  const state = insightResultState(result);
  const gaps = [...(result.gaps ?? [])].sort((a, b) => Number(b.blocking) - Number(a.blocking));

  if (state === 'unrelated') {
    return (
      <div className={`${styles.notice} ${styles.calm}`}>
        <EmptyState
          title="Заметного влияния на компанию не нашли"
          message={insight.status_reason ?? 'Можно вернуться к ленте и выбрать другое событие.'}
        />
      </div>
    );
  }

  if (state === 'empty') {
    return (
      <div className={`${styles.notice} ${styles.calm}`}>
        <EmptyState
          title="В ответе пока нет выводов"
          message="ИИ не подготовил содержательный разбор. Попробуйте ещё раз или выберите другое событие."
          action={onRetry ? { title: 'Повторить разбор', onClick: onRetry } : undefined}
        />
      </div>
    );
  }

  if (state === 'insufficient' || state === 'partial') {
    return (
      <div className={`${styles.notice} ${styles.warn}`} role="status">
        <p className={styles.title}>
          {state === 'insufficient'
            ? 'Для уверенного вывода не хватает данных'
            : 'Разбор готов частично'}
        </p>
        <p>
          {execution !== 'completed' ? `${EXECUTION[execution]} ` : ''}
          {hasInsightContent(result)
            ? 'Доступная часть разбора показана ниже.'
            : 'Персональные выводы пока не сформированы.'}
        </p>
        {gaps.length > 0 && (
          <details className={styles.details}>
            <summary>
              Чего не хватило · {gaps.length}{' '}
              {plural(gaps.length, { one: 'пункт', few: 'пункта', many: 'пунктов' })}
            </summary>
            <ul className={styles.gaps}>
              {gaps.map((gap) => (
                <li key={`${gap.kind}:${gap.missing}`} className={styles.gap}>
                  <p className={styles.gapTitle}>{gap.missing}</p>
                  <p>{gap.consequence}</p>
                  {gap.remedy && <p className={styles.remedy}>Что можно сделать: {gap.remedy}</p>}
                </li>
              ))}
            </ul>
          </details>
        )}
        {execution !== 'completed' && onRetry && (
          <ButtonDesktop size={40} view="secondary" onClick={onRetry}>
            Повторить разбор
          </ButtonDesktop>
        )}
      </div>
    );
  }

  return null;
}
