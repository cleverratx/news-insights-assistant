import { useId } from 'react';
import type { MouseEvent } from 'react';

import type { GeneratedInsight, LabeledStatement } from '@/shared/api/types';

import styles from './AskPanel.module.css';

function revealSource(event: MouseEvent<HTMLAnchorElement>, id: string) {
  const element = document.getElementById(id);
  if (!(element instanceof HTMLDetailsElement)) return;
  event.preventDefault();
  element.open = true;
  element.closest('details[data-answer-basis]')?.setAttribute('open', '');
  element.scrollIntoView({ block: 'nearest', behavior: 'auto' });
  element.querySelector('summary')?.focus({ preventScroll: true });
}

export function AnswerContent({ result }: { result: GeneratedInsight }) {
  const prefix = useId();
  const { presentation, insight } = result;
  const disclaimer =
    'disclaimer' in result && typeof result.disclaimer === 'string'
      ? result.disclaimer
      : 'Ответ подготовлен ИИ. Не является инвестиционной рекомендацией.';
  const originals = presentation.originals ?? [];
  const sourceIds = new Map(
    originals.map((source, index) => [source.id, `${prefix}-source-${index}`]),
  );
  const rows = [
    ...(presentation.facts ?? []),
    ...(presentation.bank_position ?? []),
    ...(presentation.ai_conclusions ?? []),
    ...(presentation.client_context ?? []),
  ];
  const statements = new Map(rows.map((row) => [row.statement.id, row]));
  const blocks = presentation.blocks.filter((block) =>
    ['relevance', 'consequences', 'actions'].includes(block.id),
  );
  const hasAnswer = blocks.some((block) => block.statement_ids?.some((id) => statements.has(id)));
  const facts = [...(presentation.facts ?? []), ...(presentation.bank_position ?? [])];
  const warnings = [
    ...new Set([insight.status_reason, ...(result.warnings ?? [])].filter(Boolean)),
  ];

  function statement(row: LabeledStatement) {
    return (
      <div key={row.statement.id} className={styles.statement}>
        <span className={styles.origin} data-origin={row.statement.origin}>
          {row.label}
        </span>
        <p>
          {row.statement.text}{' '}
          {row.source_ids.map((source) => {
            const id = sourceIds.get(source);
            return id ? (
              <a
                key={source}
                className={styles.citation}
                href={`#${id}`}
                aria-label={`Источник ${source}`}
                onClick={(event) => revealSource(event, id)}
              >
                [{source}]
              </a>
            ) : null;
          })}
        </p>
      </div>
    );
  }

  return (
    <div className={styles.answer}>
      {warnings.map((warning) => (
        <p key={warning} className={styles.notice}>
          {warning}
        </p>
      ))}
      {!hasAnswer && !warnings.length && (
        <p>
          Агент не сформировал проверенный ответ на этот вопрос. Попробуйте уточнить формулировку.
        </p>
      )}
      {blocks.map((block) => {
        const content = (block.statement_ids ?? [])
          .map((id) => statements.get(id))
          .filter((row): row is LabeledStatement => Boolean(row));
        return content.length ? (
          <section className={styles.answerBlock} key={block.id}>
            <h4>{block.title}</h4>
            {content.map(statement)}
          </section>
        ) : null;
      })}
      {facts.length > 0 && (
        <details className={styles.basis}>
          <summary>Факты и позиция банка</summary>
          <div className={styles.supporting}>{facts.map(statement)}</div>
        </details>
      )}
      {originals.length > 0 && (
        <details className={styles.basis} data-answer-basis>
          <summary>Источники ответа · {originals.length}</summary>
          <div className={styles.supporting}>
            {originals.map((source) => (
              <details className={styles.source} id={sourceIds.get(source.id)} key={source.id}>
                <summary>{source.title}</summary>
                {source.quote && <blockquote>{source.quote}</blockquote>}
                {source.url && (
                  <a href={source.url} target="_blank" rel="noreferrer">
                    Открыть первоисточник
                  </a>
                )}
              </details>
            ))}
          </div>
        </details>
      )}
      <p className={styles.disclaimer}>{disclaimer}</p>
    </div>
  );
}
