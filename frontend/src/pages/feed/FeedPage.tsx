import { useCallback, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router';
import { ButtonDesktop } from '@alfalab/core-components-button/desktop';
import { Typography } from '@alfalab/core-components-typography';

import { useShell } from '@/app/shell-context';
import { apiGet, apiPost, errorMessage, isAbort } from '@/shared/api/client';
import type {
  AgentTrace,
  Feed,
  FeedItem,
  GeneratedInsight,
  InsightRequest,
  RelatedFeed,
} from '@/shared/api/types';
import { useCachedApi } from '@/shared/api/cache';
import { feedPath } from '@/shared/api/feed';
import { useApi } from '@/shared/api/useApi';
import { isRole, ROLE_TITLES, type Role } from '@/shared/lib/demo';
import { markSeen } from '@/shared/lib/seen';
import { ErrorState, Skeleton } from '@/shared/ui/StateView';

import styles from './FeedPage.module.css';

type View = 'feed' | 'related';

type Entry = Pick<FeedItem, 'news_id' | 'card' | 'summary'> &
  Partial<Pick<FeedItem, 'importance' | 'impact'>>;

type Analysis = { insight?: GeneratedInsight; trace?: AgentTrace; error?: string; busy: boolean };

const IMPACT_CLASS: Record<FeedItem['impact']['color'], string> = {
  red: styles.negative,
  orange: styles.negative,
  gray: '',
  light_green: styles.positive,
  green: styles.positive,
};

export function FeedPage() {
  const { company, role, setRole, user } = useShell();
  const [params, setParams] = useSearchParams();
  const openId = params.get('open');
  const onOpened = useCallback(
    () =>
      setParams(
        (current) => {
          const next = new URLSearchParams(current);
          next.delete('open');
          return next;
        },
        { replace: true },
      ),
    [setParams],
  );
  return (
    <div className={styles.page}>
      <Typography.Title tag="h1" view="medium">
        Новости для бизнеса
      </Typography.Title>
      <FeedView
        key={`${company}:${role ?? ''}`}
        company={company}
        role={role}
        login={user.login}
        openId={openId}
        onRole={setRole}
        onOpened={onOpened}
      />
    </div>
  );
}

type FeedViewProps = {
  company: string;
  role: Role | null;
  login: string;
  openId: string | null;
  onRole: (role: Role | null) => void;
  onOpened: () => void;
};

function reveal(element: HTMLElement | null) {
  const smooth = !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  requestAnimationFrame(() =>
    element?.scrollIntoView({ behavior: smooth ? 'smooth' : 'auto', block: 'start' }),
  );
}

function FeedView({ company, role, login, openId, onRole, onOpened }: FeedViewProps) {
  const [view, setView] = useState<View>('feed');
  const [background, setBackground] = useState(false);
  const [analysis, setAnalysis] = useState<Analysis>({ busy: false });
  const controller = useRef<AbortController | null>(null);
  const panel = useRef<HTMLDivElement>(null);

  useEffect(() => () => controller.current?.abort(), []);

  const base = `/companies/${encodeURIComponent(company)}`;
  const feed = useCachedApi<Feed>(view === 'feed' ? feedPath(company, role, background) : null);
  const related = useApi<RelatedFeed>(view === 'related' ? `${base}/related-feed` : null);
  const current = view === 'feed' ? feed : related;
  const entries: Entry[] = current.data?.items ?? [];

  const start = () => {
    controller.current?.abort();
    controller.current = new AbortController();
    return controller.current.signal;
  };

  const fail = (error: unknown) => {
    if (!isAbort(error))
      setAnalysis((state) => ({ ...state, busy: false, error: errorMessage(error) }));
  };

  const runInsight = useCallback(
    (body: InsightRequest) => {
      controller.current?.abort();
      const current = new AbortController();
      controller.current = current;
      setAnalysis({ busy: true });
      reveal(panel.current);
      apiPost<GeneratedInsight>(
        `/companies/${encodeURIComponent(company)}/insights`,
        body,
        current.signal,
      ).then(
        (insight) => {
          setAnalysis({ insight, busy: false });
          reveal(panel.current);
        },
        (error: unknown) => {
          if (!isAbort(error)) setAnalysis({ busy: false, error: errorMessage(error) });
        },
      );
    },
    [company],
  );

  const feedData = feed.data;

  useEffect(() => {
    if (feedData)
      markSeen(
        login,
        company,
        (feedData.items ?? []).map((item) => item.news_id),
      );
  }, [feedData, login, company]);

  useEffect(() => {
    if (!openId || !feedData) return;
    onOpened();
    const entry = feedData.items?.find((item) => item.news_id === openId);
    if (entry) {
      runInsight({
        news_ids: [entry.news_id],
        role,
        feed_snapshot_id: feedData.snapshot_id,
        refresh: false,
      });
    }
  }, [openId, feedData, role, runInsight, onOpened]);

  const open = (entry: Entry) =>
    runInsight({
      news_ids: [entry.news_id],
      role,
      feed_snapshot_id: view === 'feed' ? feed.data?.snapshot_id : null,
      refresh: false,
    });

  const showTrace = (insightId: string) => {
    const signal = start();
    setAnalysis((state) => ({ ...state, busy: true, error: undefined }));
    apiGet<AgentTrace>(`/insights/${encodeURIComponent(insightId)}/trace`, signal).then(
      (trace) => setAnalysis((state) => ({ ...state, trace, busy: false })),
      fail,
    );
  };

  return (
    <>
      <div className={styles.filters}>
        <label className={styles.field}>
          Должность
          <select
            value={role ?? ''}
            onChange={(event) => onRole(isRole(event.target.value) ? event.target.value : null)}
          >
            <option value="">Вся компания</option>
            {Object.entries(ROLE_TITLES).map(([key, title]) => (
              <option key={key} value={key}>
                {title}
              </option>
            ))}
          </select>
        </label>
        <label className={styles.field}>
          Новости
          <select value={view} onChange={(event) => setView(event.target.value as View)}>
            <option value="feed">Для компании и должности</option>
            <option value="related">Конкуренты и контрагенты</option>
          </select>
        </label>
        {view === 'feed' && (
          <label className={styles.check}>
            <input
              type="checkbox"
              checked={background}
              onChange={(event) => setBackground(event.target.checked)}
            />
            Показывать новости низкой важности
          </label>
        )}
      </div>

      {current.loading && (
        <div className={styles.plate}>
          <Skeleton lines={4} />
        </div>
      )}
      {current.error && (
        <div className={styles.plate}>
          <ErrorState
            title="Не удалось загрузить новости"
            message={current.error}
            onRetry={current.retry}
          />
        </div>
      )}
      {current.data?.warnings?.map((warning) => (
        <p key={warning} className={styles.warning}>
          {warning}
        </p>
      ))}
      {current.data && entries.length === 0 && (
        <p className={styles.plate}>
          За выбранный период значимых событий не нашлось. Попробуйте показать новости низкой
          важности или выбрать другую должность.
        </p>
      )}

      <div className={styles.cards}>
        {entries.map((entry) => (
          <article key={entry.news_id} className={styles.card}>
            {entry.card.image && <img src={entry.card.image} alt="" loading="lazy" />}
            <h2 className={styles.cardTitle}>{entry.card.title}</h2>
            {entry.summary && <p className={styles.text}>{entry.summary}</p>}
            {entry.importance && (
              <p className={styles.text}>
                {entry.importance.title}: {entry.importance.score}/10
              </p>
            )}
            {entry.impact && (
              <p className={`${styles.text} ${IMPACT_CLASS[entry.impact.color]}`}>
                Влияние: {entry.impact.score > 0 ? '+' : ''}
                {entry.impact.score} — {entry.impact.rationale}
              </p>
            )}
            <div className={styles.cardActions}>
              <ButtonDesktop
                view="secondary"
                size={40}
                disabled={analysis.busy}
                onClick={() => open(entry)}
              >
                Разобрать событие
              </ButtonDesktop>
              {entry.card.url && (
                <a href={entry.card.url} target="_blank" rel="noreferrer">
                  Оригинальная статья
                </a>
              )}
            </div>
          </article>
        ))}
      </div>

      <div ref={panel} className={styles.panel} aria-live="polite">
        {analysis.busy && !analysis.insight && (
          <div className={styles.plate}>
            <p className={styles.text}>Разбираем событие…</p>
            <Skeleton lines={6} />
          </div>
        )}
        {analysis.error && (
          <div className={styles.plate}>
            <ErrorState title="Не удалось выполнить запрос" message={analysis.error} />
          </div>
        )}
        {analysis.insight && (
          <InsightView
            result={analysis.insight}
            trace={analysis.trace}
            busy={analysis.busy}
            onTrace={showTrace}
          />
        )}
      </div>
    </>
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
    <section className={styles.plate}>
      <Typography.Title tag="h2" view="small">
        {insight.headline}
      </Typography.Title>
      {insight.status_reason && <p className={styles.text}>{insight.status_reason}</p>}
      {result.ranking?.map((rank) => (
        <p key={rank.news_id} className={styles.text}>
          {rank.importance.title}: {rank.importance.score}/10. Влияние: {rank.impact.score}.{' '}
          {rank.impact.rationale}
        </p>
      ))}
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
    </section>
  );
}
