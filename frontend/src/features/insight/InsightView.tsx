import { numberSources, sourceAnchor, type SourceRef } from '@/features/trace/sources';
import type { components } from '@/shared/api/schema';
import type { GeneratedInsight, LabeledStatement } from '@/shared/api/types';
import { OriginMark } from '@/shared/ui/OriginMark';

import { OfferCard } from './OfferCard';
import styles from './InsightView.module.css';

type Schemas = components['schemas'];
type Block = Schemas['InsightBlock'];

const HORIZON: Record<NonNullable<Schemas['Impact']['horizon']>, string> = {
  short: 'в ближайшие месяцы',
  medium: 'в течение года',
  long: 'в долгосрочной перспективе',
};

const MAGNITUDE: Record<Schemas['Significance'], string> = {
  high: 'сильное влияние',
  medium: 'умеренное влияние',
  low: 'слабое влияние',
};

const ASSUMPTION_HINT = 'Вывод логичен, но прямо в источниках не подтверждён';

const ACTION_KIND: Record<Schemas['ActionKind'], string> = {
  action: 'Действие',
  question: 'Что выяснить',
  scenario: 'Сценарий',
  bank_product: 'Продукт банка',
};

type Context = {
  rows: Map<string, LabeledStatement>;
  numbers: Map<string, number>;
  sources: Map<string, SourceRef>;
  rewrites: Map<string, string>;
  onSource: (number: number) => void;
};

type Props = { result: GeneratedInsight; onSource: (number: number) => void };

export function InsightView({ result, onSource }: Props) {
  const { insight, presentation } = result;
  const originals = presentation.originals ?? [];
  const all = [
    ...(presentation.facts ?? []),
    ...(presentation.bank_position ?? []),
    ...(presentation.ai_conclusions ?? []),
    ...(presentation.client_context ?? []),
  ];
  const context: Context = {
    rows: new Map(all.map((row) => [row.statement.id, row])),
    numbers: numberSources(originals),
    sources: new Map(originals.map((source) => [source.id, source])),
    rewrites: new Map(
      (result.role_view?.rewrites ?? []).map((item) => [item.statement_id, item.text]),
    ),
    onSource,
  };
  const risks = new Set((insight.risks ?? []).map((item) => item.id));

  return (
    <div className={styles.view}>
      {presentation.blocks
        .filter((block) => block.id !== 'basis')
        .map((block) => (
          <section key={block.id} className={styles.block} aria-labelledby={`block-${block.id}`}>
            <h2 id={`block-${block.id}`} className={styles.blockTitle}>
              {block.title}
            </h2>
            {block.id === 'consequences' ? (
              <Consequences block={block} context={context} risks={risks} />
            ) : (
              <Statements ids={block.statement_ids ?? []} context={context} />
            )}
            {block.id === 'actions' && result.offer && <OfferCard offer={result.offer} />}
            {block.empty_reason && !hasContent(block, result) && (
              <p className={styles.empty}>{block.empty_reason}</p>
            )}
          </section>
        ))}
    </div>
  );
}

function hasContent(block: Block, result: GeneratedInsight): boolean {
  if ((block.statement_ids ?? []).length > 0) return true;
  if (block.id === 'actions') return Boolean(result.offer);
  return false;
}

function Statements({ ids, context }: { ids: string[]; context: Context }) {
  const rows = ids.flatMap((id) => {
    const row = context.rows.get(id);
    return row ? [row] : [];
  });
  if (rows.length === 0) return null;
  return (
    <ul className={styles.statements}>
      {rows.map((row) => (
        <li key={row.statement.id}>
          <Statement row={row} context={context} />
        </li>
      ))}
    </ul>
  );
}

function Statement({ row, context }: { row: LabeledStatement; context: Context }) {
  const { statement } = row;
  const assumption = statement.support === 'assumption' || statement.confidence === 'low';
  const text = context.rewrites.get(statement.id) ?? statement.text;
  const details = [
    'horizon' in statement && statement.horizon ? HORIZON[statement.horizon] : null,
    'magnitude' in statement && statement.magnitude ? MAGNITUDE[statement.magnitude] : null,
  ].filter(Boolean);

  return (
    <div
      className={`${styles.statement} ${statement.origin === 'model' ? styles.aiStatement : ''}`}
    >
      <div className={styles.marks}>
        <OriginMark origin={statement.origin} />
        {'kind' in statement && <span className={styles.kind}>{ACTION_KIND[statement.kind]}</span>}
        {assumption && (
          <span className={styles.assumption} title={ASSUMPTION_HINT}>
            предположение
          </span>
        )}
      </div>
      <p className={styles.text}>
        <span
          className={assumption ? styles.assumed : undefined}
          title={assumption ? ASSUMPTION_HINT : undefined}
        >
          {text}
        </span>
        {[...new Map(row.source_ids.map((id) => [context.numbers.get(id), id])).entries()].map(
          ([number, id]) => {
            if (!number) return null;
            const source = context.sources.get(id);
            return (
              <a
                key={number}
                className={styles.ref}
                href={`#${sourceAnchor(number)}`}
                onClick={(event) => {
                  event.preventDefault();
                  context.onSource(number);
                }}
                title={source?.quote ?? source?.title}
                aria-label={`Источник ${number}: ${source?.title ?? ''}`}
              >
                {number}
              </a>
            );
          },
        )}
      </p>
      {details.length > 0 && <p className={styles.details}>{details.join(' · ')}</p>}
    </div>
  );
}

type ConsequencesProps = { block: Block; context: Context; risks: Set<string> };

function Consequences({ block, context, risks }: ConsequencesProps) {
  const ids = block.statement_ids ?? [];
  if (ids.length === 0) return null;
  const riskIds = ids.filter((id) => risks.has(id));
  const chanceIds = ids.filter((id) => !risks.has(id));
  return (
    <div className={styles.columns}>
      <div className={styles.column}>
        <h3 className={`${styles.columnTitle} ${styles.riskTitle}`}>
          <span aria-hidden="true">↓</span> Риски
        </h3>
        {riskIds.length > 0 ? (
          <Statements ids={riskIds} context={context} />
        ) : (
          <p className={styles.empty}>Заметных рисков не найдено</p>
        )}
      </div>
      <div className={styles.column}>
        <h3 className={`${styles.columnTitle} ${styles.chanceTitle}`}>
          <span aria-hidden="true">↑</span> Возможности
        </h3>
        {chanceIds.length > 0 ? (
          <Statements ids={chanceIds} context={context} />
        ) : (
          <p className={styles.empty}>Заметных возможностей не найдено</p>
        )}
      </div>
    </div>
  );
}
