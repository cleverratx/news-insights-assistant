import type { components } from '@/shared/api/schema';
import type { GeneratedInsight, LabeledStatement } from '@/shared/api/types';
import { OriginMark, ORIGIN_TITLES, type Origin } from '@/shared/ui/OriginMark';

import { OfferCard } from './OfferCard';
import styles from './InsightView.module.css';

type Schemas = components['schemas'];
type Block = Schemas['InsightBlock'];
type SourceRef = Schemas['SourceRef'];

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

const ACTION_KIND: Record<Schemas['ActionKind'], string> = {
  action: 'Действие',
  question: 'Что выяснить',
  scenario: 'Сценарий',
  bank_product: 'Продукт банка',
};

const SOURCE_TYPE: Record<Schemas['SourceType'], string> = {
  news: 'Новость',
  bank_analytics: 'Аналитика Альфа-Банка',
  profile_feature: 'Данные вашей компании',
};

function sourceKey(source: SourceRef): string {
  return source.url ?? `${source.type}:${source.title}`;
}

function numberSources(originals: SourceRef[]): Map<string, number> {
  const byKey = new Map<string, number>();
  return new Map(
    originals.map((source) => {
      const key = sourceKey(source);
      if (!byKey.has(key)) byKey.set(key, byKey.size + 1);
      return [source.id, byKey.get(key) ?? 0];
    }),
  );
}

type Context = {
  rows: Map<string, LabeledStatement>;
  numbers: Map<string, number>;
  sources: Map<string, SourceRef>;
  rewrites: Map<string, string>;
};

export function InsightView({ result }: { result: GeneratedInsight }) {
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
  };
  const risks = new Set((insight.risks ?? []).map((item) => item.id));
  const origins = new Set(all.map((row) => row.statement.origin));
  const hasBank = (presentation.bank_position ?? []).length > 0;

  return (
    <div className={styles.view}>
      <div className={styles.legend}>
        <span className={styles.legendTitle}>Откуда утверждения:</span>
        {(Object.keys(ORIGIN_TITLES) as Origin[])
          .filter((origin) => origins.has(origin))
          .map((origin) => (
            <OriginMark key={origin} origin={origin} />
          ))}
        {!hasBank && (
          <p className={styles.notBank}>
            Это не официальная позиция банка: выводы сформированы моделью по открытым источникам.
          </p>
        )}
      </div>
      {presentation.blocks.map((block) => (
        <section key={block.id} className={styles.block} aria-labelledby={`block-${block.id}`}>
          <h2 id={`block-${block.id}`} className={styles.blockTitle}>
            {block.title}
          </h2>
          {block.id === 'consequences' ? (
            <Consequences block={block} context={context} risks={risks} />
          ) : block.id === 'basis' ? (
            <Sources block={block} originals={originals} numbers={context.numbers} />
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
  if (block.id === 'basis') return (result.presentation.originals ?? []).length > 0;
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
  const assumption = statement.support === 'assumption';
  const text = context.rewrites.get(statement.id) ?? statement.text;
  const details = [
    'horizon' in statement && statement.horizon ? HORIZON[statement.horizon] : null,
    'magnitude' in statement && statement.magnitude ? MAGNITUDE[statement.magnitude] : null,
  ].filter(Boolean);

  return (
    <div className={styles.statement}>
      <div className={styles.marks}>
        <OriginMark origin={statement.origin} />
        {'kind' in statement && <span className={styles.kind}>{ACTION_KIND[statement.kind]}</span>}
        {assumption && <span className={styles.assumption}>предположение</span>}
        {statement.confidence === 'low' && <span className={styles.kind}>уверенность низкая</span>}
      </div>
      <p className={styles.text}>
        <span className={assumption ? styles.assumed : undefined}>{text}</span>
        {[...new Map(row.source_ids.map((id) => [context.numbers.get(id), id])).entries()].map(
          ([number, id]) => {
            if (!number) return null;
            const source = context.sources.get(id);
            return (
              <a
                key={number}
                className={styles.ref}
                href={`#source-${number}`}
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

type SourcesProps = { block: Block; originals: SourceRef[]; numbers: Map<string, number> };

function Sources({ block, originals, numbers }: SourcesProps) {
  const wanted = new Set(block.source_ids ?? []);
  const list = wanted.size > 0 ? originals.filter((source) => wanted.has(source.id)) : originals;
  const groups = new Map<number, SourceRef[]>();
  list.forEach((source) => {
    const number = numbers.get(source.id) ?? 0;
    groups.set(number, [...(groups.get(number) ?? []), source]);
  });
  if (groups.size === 0) return null;
  return (
    <ol className={styles.sources}>
      {[...groups.entries()].map(([number, sources]) => {
        const [first] = sources;
        const quotes = sources.flatMap((source) => (source.quote ? [source.quote] : []));
        return (
          <li key={number} id={`source-${number}`} className={styles.source}>
            <span className={styles.sourceNumber}>{number}</span>
            <div className={styles.sourceBody}>
              <p className={styles.sourceMeta}>{SOURCE_TYPE[first.type]}</p>
              <p className={styles.sourceTitle}>{first.title}</p>
              {quotes.map((quote) => (
                <blockquote key={quote} className={styles.quote}>
                  {quote}
                </blockquote>
              ))}
              {first.url && (
                <a href={first.url} target="_blank" rel="noreferrer" className={styles.sourceLink}>
                  Открыть оригинал
                </a>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
