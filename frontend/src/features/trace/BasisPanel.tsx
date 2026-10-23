import { useEffect, useId, useState, type ReactNode } from 'react';
import { ChevronDownMIcon } from '@alfalab/icons-glyph/ChevronDownMIcon';

import type { AgentTrace, GeneratedInsight } from '@/shared/api/types';
import { useApi } from '@/shared/api/useApi';
import { plural } from '@/shared/lib/format';
import { ErrorState, Skeleton } from '@/shared/ui/StateView';

import { AgentSteps } from './AgentSteps';
import styles from './BasisPanel.module.css';
import { SOURCE_TYPE, numberSources, sourceAnchor, type SourceRef } from './sources';

type Feature = NonNullable<AgentTrace['profile_features']>[number];
type Statement = { source_ids: string[] };
type Forms = { one: string; few: string; many: string };

type Document = {
  number: number;
  refId: string;
  type: SourceRef['type'];
  title: string;
  url?: string | null;
  quotes: string[];
  uses: number;
};

type Seen = { id: string; title: string; detail: string };

const SECTIONS = [
  'what_happened',
  'why_it_matters',
  'risks',
  'opportunities',
  'actions',
  'bank_view',
] as const;

const reduceMotion = () =>
  typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

const day = new Intl.DateTimeFormat('ru-RU', {
  day: 'numeric',
  month: 'long',
  year: 'numeric',
  timeZone: 'Europe/Moscow',
});

const NBSP = String.fromCharCode(160);

function counted(count: number, forms: Forms): string {
  return `${count}${NBSP}${plural(count, forms)}`;
}

function statements(result: GeneratedInsight): Statement[] {
  return SECTIONS.flatMap((section) => result.insight[section] ?? []);
}

function collect(result: GeneratedInsight): { documents: Document[]; features: Document[] } {
  const originals = result.presentation.originals ?? [];
  const numbers = numberSources(originals);
  const uses = new Map<number, number>();
  statements(result).forEach((statement) => {
    new Set(statement.source_ids.map((id) => numbers.get(id) ?? 0)).forEach((number) =>
      uses.set(number, (uses.get(number) ?? 0) + 1),
    );
  });
  const groups = new Map<number, Document>();
  originals.forEach((source) => {
    const number = numbers.get(source.id) ?? 0;
    const found = groups.get(number);
    if (found) {
      if (source.quote && !found.quotes.includes(source.quote)) found.quotes.push(source.quote);
      return;
    }
    groups.set(number, {
      number,
      refId: source.ref_id,
      type: source.type,
      title: source.title,
      url: source.url,
      quotes: source.quote ? [source.quote] : [],
      uses: uses.get(number) ?? 0,
    });
  });
  const all = [...groups.values()].sort((a, b) => a.number - b.number);
  return {
    documents: all.filter((item) => item.type !== 'profile_feature'),
    features: all.filter((item) => item.type === 'profile_feature'),
  };
}

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}

function seenDocuments(trace: AgentTrace, cited: Set<string>): Seen[] {
  const found = new Map<string, Seen>();
  (trace.observations ?? []).forEach((entry) => {
    const data = record(record(entry.observation)?.data);
    const hits = Array.isArray(data?.hits) ? data.hits : [];
    hits.forEach((hit) => {
      const item = record(hit);
      const id = text(item?.doc_id);
      const title = text(item?.title);
      if (!id || !title || cited.has(id) || found.has(id)) return;
      const kind = item?.kind === 'bank_analytics' ? 'Аналитика Альфа-Банка' : 'Новость';
      const date = text(item?.published_at);
      found.set(id, {
        id,
        title,
        detail: [kind, date ? day.format(new Date(date)) : null].filter(Boolean).join(' · '),
      });
    });
  });
  return [...found.values()];
}

function fragmentQuotes(trace: AgentTrace | undefined, sources: SourceRef[], number: number) {
  if (!trace) return [];
  const numbers = numberSources(sources);
  const documents = new Map((trace.documents ?? []).map((doc) => [doc.ref_id, doc]));
  return sources
    .filter((source) => numbers.get(source.id) === number && !source.quote && source.fragment_id)
    .flatMap((source) => {
      const chunk = documents
        .get(source.ref_id)
        ?.chunks.find((item) => item.fragment_id === source.fragment_id);
      return chunk ? [chunk.text] : [];
    });
}

type Props = {
  result: GeneratedInsight;
  open: boolean;
  focus: number | null;
  onToggle: (open: boolean) => void;
};

export function BasisPanel({ result, open, focus, onToggle }: Props) {
  const id = useId();
  const [loaded, setLoaded] = useState(open);
  if (open && !loaded) setLoaded(true);
  const trace = useApi<AgentTrace>(
    loaded ? `/insights/${encodeURIComponent(result.insight.id)}/trace` : null,
  );
  const { documents, features } = collect(result);
  const originals = result.presentation.originals ?? [];

  useEffect(() => {
    if (!open || focus == null) return;
    document
      .getElementById(sourceAnchor(focus))
      ?.scrollIntoView({ behavior: reduceMotion() ? 'auto' : 'smooth', block: 'center' });
  }, [open, focus]);

  const summary = [
    counted(documents.length, { one: 'документ', few: 'документа', many: 'документов' }),
    counted(features.length, {
      one: 'признак компании',
      few: 'признака компании',
      many: 'признаков компании',
    }),
    trace.data
      ? counted(trace.data.steps?.length ?? 0, {
          one: 'шаг агента',
          few: 'шага агента',
          many: 'шагов агента',
        })
      : 'шаги агента',
  ].join(' · ');

  return (
    <section id="basis" className={styles.panel} aria-labelledby={`${id}-title`}>
      <button
        type="button"
        className={styles.head}
        aria-expanded={open}
        aria-controls={id}
        onClick={() => onToggle(!open)}
      >
        <span className={styles.headText}>
          <span id={`${id}-title`} className={styles.title}>
            На чём основан вывод
          </span>
          <span className={styles.summary}>{summary}</span>
        </span>
        <span className={styles.headIcon} aria-hidden="true">
          <ChevronDownMIcon className={`${styles.chevron} ${open ? styles.chevronOpen : ''}`} />
        </span>
      </button>
      {open && (
        <div id={id} className={styles.body}>
          <Group title="Документы" hint="Номера — те же, что у утверждений в разборе">
            <SourceList
              items={documents}
              focus={focus}
              extra={(number) => fragmentQuotes(trace.data, originals, number)}
            />
            {trace.data && (
              <SeenList
                items={seenDocuments(trace.data, new Set(originals.map((item) => item.ref_id)))}
              />
            )}
          </Group>
          <Group title="Данные вашей компании" hint="Признаки из профиля компании в банке">
            <FeatureList items={features} focus={focus} trace={trace.data} />
          </Group>
          <Group title="Как агент пришёл к выводу">
            {trace.loading && <Skeleton lines={3} />}
            {trace.error && (
              <ErrorState
                title="Не удалось загрузить шаги"
                message={trace.error}
                onRetry={trace.retry}
              />
            )}
            {trace.data && <AgentSteps trace={trace.data} />}
          </Group>
          <Service result={result} trace={trace.data} />
        </div>
      )}
    </section>
  );
}

function Group({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return (
    <div className={styles.group}>
      <h3 className={styles.groupTitle}>
        {title}
        {hint && <span className={styles.groupHint}>{hint}</span>}
      </h3>
      {children}
    </div>
  );
}

function Uses({ count }: { count: number }) {
  if (count === 0) return null;
  return (
    <span className={styles.uses}>
      {counted(count, { one: 'утверждение', few: 'утверждения', many: 'утверждений' })}
    </span>
  );
}

type SourceListProps = {
  items: Document[];
  focus: number | null;
  extra: (number: number) => string[];
};

function SourceList({ items, focus, extra }: SourceListProps) {
  if (items.length === 0) {
    return <p className={styles.empty}>Документы в выводе не цитируются.</p>;
  }
  return (
    <ol className={styles.sources}>
      {items.map((item) => {
        const quotes = item.quotes.length > 0 ? item.quotes : extra(item.number);
        return (
          <li
            key={item.number}
            id={sourceAnchor(item.number)}
            className={`${styles.source} ${focus === item.number ? styles.focused : ''}`}
          >
            <span className={styles.number}>{item.number}</span>
            <div className={styles.sourceBody}>
              <p className={styles.sourceMeta}>
                <span className={item.type === 'bank_analytics' ? styles.bank : undefined}>
                  {SOURCE_TYPE[item.type]}
                </span>
                <Uses count={item.uses} />
              </p>
              <p className={styles.sourceTitle}>{item.title}</p>
              {quotes.map((quote) => (
                <blockquote key={quote} className={styles.quote}>
                  {quote}
                </blockquote>
              ))}
              {item.url && (
                <a href={item.url} target="_blank" rel="noreferrer" className={styles.link}>
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

function SeenList({ items }: { items: Seen[] }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  if (items.length === 0) return null;
  return (
    <div className={styles.seen}>
      <button
        type="button"
        className={styles.more}
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((value) => !value)}
      >
        Агент просмотрел ещё{' '}
        {counted(items.length, { one: 'материал', few: 'материала', many: 'материалов' })}, но не
        взял в вывод
      </button>
      {open && (
        <ul id={id} className={styles.seenList}>
          {items.map((item) => (
            <li key={item.id}>
              <span className={styles.seenTitle}>{item.title}</span>
              <span className={styles.muted}>{item.detail}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

type FeatureListProps = { items: Document[]; focus: number | null; trace?: AgentTrace };

function FeatureList({ items, focus, trace }: FeatureListProps) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const all: Feature[] = trace?.profile_features ?? [];
  const labels = new Map(all.map((feature) => [feature.id, feature.label]));
  const used = new Set(items.map((item) => item.refId));
  const rest = all.filter((feature) => !used.has(feature.id));

  return (
    <>
      {items.length === 0 ? (
        <p className={styles.empty}>Вывод не ссылается на данные компании.</p>
      ) : (
        <ul className={styles.features}>
          {items.map((item) => (
            <li
              key={item.number}
              id={sourceAnchor(item.number)}
              className={`${styles.feature} ${focus === item.number ? styles.focused : ''}`}
            >
              <span className={styles.number}>{item.number}</span>
              <span className={styles.featureText}>{labels.get(item.refId) ?? item.title}</span>
              <Uses count={item.uses} />
            </li>
          ))}
        </ul>
      )}
      {rest.length > 0 && (
        <div className={styles.seen}>
          <button
            type="button"
            className={styles.more}
            aria-expanded={open}
            aria-controls={id}
            onClick={() => setOpen((value) => !value)}
          >
            Ещё {counted(rest.length, { one: 'признак', few: 'признака', many: 'признаков' })} агент
            видел, но в выводе не использовал
          </button>
          {open && (
            <ul id={id} className={styles.chips}>
              {rest.map((feature) => (
                <li key={feature.id} className={styles.chip}>
                  {feature.label}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </>
  );
}

const rubles = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
const secondsFormat = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 });

function Service({ result, trace }: { result: GeneratedInsight; trace?: AgentTrace }) {
  const usage = result.usage;
  const cost = Number(usage.known_cost_rub);
  const parts = [
    trace ? `Разбор занял ${secondsFormat.format(trace.elapsed_seconds)}${NBSP}с` : null,
    counted(usage.calls, { one: 'вызов модели', few: 'вызова модели', many: 'вызовов модели' }),
    (usage.models ?? []).join(', ') || null,
    Number.isFinite(cost) && cost > 0 ? `${rubles.format(cost)}${NBSP}₽` : null,
  ].filter(Boolean);
  return <p className={styles.service}>{parts.join(' · ')}</p>;
}
