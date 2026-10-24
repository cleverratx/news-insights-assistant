import { useId, useRef } from 'react';
import { ChevronDownMIcon } from '@alfalab/icons-glyph/ChevronDownMIcon';

import { StatusNotice } from '@/features/insight/StatusNotice';
import { useInsight } from '@/features/insight/useInsight';
import { SOURCE_TYPE } from '@/features/trace/sources';
import type { GeneratedInsight, LabeledStatement } from '@/shared/api/types';
import type { Role } from '@/shared/lib/demo';
import { AnalysisProgress } from '@/shared/ui/AnalysisProgress';
import { OriginMark } from '@/shared/ui/OriginMark';
import { EmptyState, ErrorState } from '@/shared/ui/StateView';

import { relatedContextMatches, relatedStatements, type CompanyMention } from './model';
import styles from './CompetitorsPage.module.css';

type Props = { company: string; role: Role | null; mention: CompanyMention };

export function RelatedAnalysis({ company, role, mention }: Props) {
  const insight = useInsight({
    company,
    role,
    newsId: mention.news_id,
    snapshot: null,
    relatedCompanyId: mention.id,
  });
  const result = insight.data;

  if (insight.loading) return <AnalysisProgress key={insight.key} onCancel={insight.cancel} />;
  if (insight.cancelled)
    return (
      <EmptyState
        title="Ожидание остановлено"
        action={{ title: 'Запросить снова', onClick: insight.retry }}
      />
    );
  if (insight.error)
    return (
      <ErrorState
        title="Не удалось оценить влияние"
        message={insight.error}
        onRetry={insight.retry}
      />
    );
  if (!result) return null;
  if (!relatedContextMatches(result, company, mention, role))
    return (
      <ErrorState
        title="Не удалось получить разбор для этой компании"
        message="Ответ не соответствует выбранному событию или компании. Повторите запрос."
        onRetry={insight.retry}
      />
    );
  return <AnalysisResult result={result} onRetry={insight.retry} />;
}

function AnalysisResult({ result, onRetry }: { result: GeneratedInsight; onRetry: () => void }) {
  const prefix = useId();
  const sources = useRef<HTMLDetailsElement>(null);
  const digest = relatedStatements(result, 'digest');
  const meaning = relatedStatements(result, 'meaning');
  const originals = result.presentation.originals ?? [];
  const numbers = new Map(originals.map((source, index) => [source.id, index + 1]));

  function statement(row: LabeledStatement) {
    const text =
      result.role_view?.rewrites?.find((item) => item.statement_id === row.statement.id)?.text ??
      row.statement.text;
    const assumption = row.statement.support === 'assumption' || row.statement.confidence === 'low';
    return (
      <li key={row.statement.id} className={styles.statement}>
        <div className={styles.marks}>
          <OriginMark origin={row.statement.origin} />
          {assumption && <span className={styles.assumption}>Предположение</span>}
        </div>
        <p>
          {text}
          <span className={styles.citations}>
            {[...new Set(row.source_ids)].map((id) => {
              const number = numbers.get(id);
              return number ? (
                <a
                  key={id}
                  className={styles.citation}
                  href={`#${prefix}-source-${number}`}
                  aria-label={`Источник ${number}`}
                  onClick={(event) => {
                    event.preventDefault();
                    if (sources.current) sources.current.open = true;
                    const target = document.getElementById(`${prefix}-source-${number}`);
                    target?.scrollIntoView({ block: 'nearest', behavior: 'auto' });
                    target?.focus({ preventScroll: true });
                  }}
                >
                  {number}
                </a>
              ) : null;
            })}
          </span>
        </p>
      </li>
    );
  }

  return (
    <div className={styles.analysis}>
      <StatusNotice result={result} onRetry={onRetry} />
      <section className={styles.digest} aria-label="Выжимка события">
        <h3>Что произошло в компании «{result.related?.company.name}»</h3>
        {digest.length ? (
          <ul className={styles.statements}>{digest.map(statement)}</ul>
        ) : (
          <p className={styles.hint}>
            Данных для выжимки пока недостаточно. Заголовок события ведёт к исходному материалу.
          </p>
        )}
      </section>
      <section className={styles.meaning} aria-label="Влияние на наш бизнес">
        <h3>Что это значит для нас</h3>
        <p className={styles.relationText}>{result.related?.relation}</p>
        {meaning.length ? (
          <ul className={styles.statements}>{meaning.map(statement)}</ul>
        ) : (
          <p className={styles.hint}>
            Пока недостаточно данных, чтобы оценить последствия для нашей компании.
          </p>
        )}
      </section>
      <details ref={sources} className={styles.sourcePanel}>
        <summary>
          <span>Источники и основания</span>
          <span className={styles.sourceCount}>{originals.length}</span>
          <ChevronDownMIcon className={styles.sourceChevron} aria-hidden="true" />
        </summary>
        {originals.length === 0 && (
          <p className={styles.sourceEmpty}>Отдельные фрагменты источников не получены.</p>
        )}
        <ol className={styles.sources}>
          {originals.map((source, index) => (
            <li
              className={styles.source}
              id={`${prefix}-source-${index + 1}`}
              key={source.id}
              tabIndex={-1}
            >
              <span className={styles.sourceNumber}>{index + 1}</span>
              <div className={styles.sourceContent}>
                <span className={styles.sourceType}>{SOURCE_TYPE[source.type]}</span>
                {source.url ? (
                  <a
                    className={styles.sourceTitle}
                    href={source.url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {source.title}
                  </a>
                ) : (
                  <p className={styles.sourceTitle}>{source.title}</p>
                )}
                {source.quote && <blockquote>{source.quote}</blockquote>}
              </div>
            </li>
          ))}
        </ol>
      </details>
      <p className={styles.disclaimer}>
        {result.disclaimer || 'Разбор подготовлен ИИ и не является инвестиционной рекомендацией.'}
      </p>
    </div>
  );
}
