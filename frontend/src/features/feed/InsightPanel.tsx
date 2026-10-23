import { ButtonDesktop } from '@alfalab/core-components-button/desktop';
import { Typography } from '@alfalab/core-components-typography';

import type { AgentTrace, GeneratedInsight } from '@/shared/api/types';
import { ErrorState, Skeleton } from '@/shared/ui/StateView';

import styles from './InsightPanel.module.css';

export type Analysis = {
  newsId?: string;
  insight?: GeneratedInsight;
  trace?: AgentTrace;
  error?: string;
  busy: boolean;
};

type Props = {
  analysis: Analysis;
  title?: string;
  onTrace: (insightId: string) => void;
  onClose: () => void;
};

export function InsightPanel({ analysis, title, onTrace, onClose }: Props) {
  return (
    <section className={styles.panel} aria-label="Разбор события">
      <div className={styles.head}>
        <p className={styles.caption}>Разбор события{title ? `: ${title}` : ''}</p>
        <button type="button" className={styles.close} onClick={onClose}>
          Закрыть
        </button>
      </div>
      {analysis.busy && !analysis.insight && (
        <>
          <p className={styles.text}>
            Разбираем событие: собираем признаки компании, ищем позицию банка, проверяем выводы…
          </p>
          <Skeleton lines={6} />
        </>
      )}
      {analysis.error && (
        <ErrorState title="Не удалось разобрать событие" message={analysis.error} />
      )}
      {analysis.insight && (
        <InsightView
          result={analysis.insight}
          trace={analysis.trace}
          busy={analysis.busy}
          onTrace={onTrace}
        />
      )}
    </section>
  );
}

type InsightViewProps = {
  result: GeneratedInsight;
  trace?: AgentTrace;
  busy: boolean;
  onTrace: (insightId: string) => void;
};

function InsightView({ result, trace, busy, onTrace }: InsightViewProps) {
  const { insight, presentation } = result;
  const rows = [
    ...(presentation.facts ?? []),
    ...(presentation.bank_position ?? []),
    ...(presentation.ai_conclusions ?? []),
    ...(presentation.client_context ?? []),
  ];
  const statements = new Map(rows.map((row) => [row.statement.id, row]));

  return (
    <>
      <Typography.Title tag="h2" view="small">
        {insight.headline}
      </Typography.Title>
      {insight.status_reason && <p className={styles.text}>{insight.status_reason}</p>}
      {result.warnings?.map((warning) => (
        <p key={warning} className={styles.warning}>
          {warning}
        </p>
      ))}
      {presentation.blocks.map((block) => (
        <section key={block.id} className={styles.block}>
          <h3 className={styles.blockTitle}>{block.title}</h3>
          {block.empty_reason && <p className={styles.text}>{block.empty_reason}</p>}
          {block.statement_ids?.map((id) => {
            const row = statements.get(id);
            return (
              row && (
                <p key={id} className={styles.text}>
                  <strong>{row.label}: </strong>
                  {row.statement.text}{' '}
                  {row.source_ids.map((source) => (
                    <a key={source} href={`#source-${source}`}>
                      [{source}]{' '}
                    </a>
                  ))}
                </p>
              )
            );
          })}
          {block.id === 'basis' &&
            presentation.originals?.map((source) => (
              <details id={`source-${source.id}`} key={source.id} className={styles.source}>
                <summary>{source.title}</summary>
                {source.quote && <blockquote>{source.quote}</blockquote>}
                {source.url && (
                  <a href={source.url} target="_blank" rel="noreferrer">
                    Первоисточник
                  </a>
                )}
              </details>
            ))}
        </section>
      ))}
      <div>
        <ButtonDesktop
          view="secondary"
          size={40}
          disabled={busy}
          loading={busy}
          onClick={() => onTrace(insight.id)}
        >
          Показать шаги анализа
        </ButtonDesktop>
      </div>
      {trace && (
        <ol className={styles.steps}>
          {trace.steps?.map((step) => (
            <li key={step.number}>
              {step.goal} {step.tool && `(${step.tool})`} — {step.status}
            </li>
          ))}
        </ol>
      )}
    </>
  );
}
