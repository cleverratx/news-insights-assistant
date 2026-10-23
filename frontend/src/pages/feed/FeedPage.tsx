import { useCallback, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router';
import { Typography } from '@alfalab/core-components-typography';

import { useShell } from '@/app/shell-context';
import { FeedCard } from '@/features/feed/FeedCard';
import { FeedFiltersBar } from '@/features/feed/FeedFilters';
import { FeedOverview } from '@/features/feed/FeedOverview';
import { applyFilters, useFeedFilters, type FeedFilters } from '@/features/feed/filters';
import { InsightPanel, type Analysis } from '@/features/feed/InsightPanel';
import { RelatedList } from '@/features/feed/RelatedList';
import { useCachedApi } from '@/shared/api/cache';
import { apiGet, apiPost, errorMessage, isAbort } from '@/shared/api/client';
import { feedPath } from '@/shared/api/feed';
import type {
  AgentTrace,
  Feed,
  FeedItem,
  GeneratedInsight,
  InsightRequest,
} from '@/shared/api/types';
import { isRole, ROLE_TITLES, type Role } from '@/shared/lib/demo';
import { plural } from '@/shared/lib/format';
import { markSeen, useSeen } from '@/shared/lib/seen';
import { ErrorState, Skeleton } from '@/shared/ui/StateView';

import styles from './FeedPage.module.css';

function reveal(element: HTMLElement | null) {
  const smooth = !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  requestAnimationFrame(() =>
    element?.scrollIntoView({ behavior: smooth ? 'smooth' : 'auto', block: 'start' }),
  );
}

function periodTitle(days: number): string {
  if (days === 1) return 'За сутки';
  return `За ${days} ${plural(days, { one: 'день', few: 'дня', many: 'дней' })}`;
}

export function FeedPage() {
  const { company, role, setRole, user } = useShell();
  const [params, setParams] = useSearchParams();
  const feedFilters = useFeedFilters();
  const openId = params.get('open');
  const onOpened = useCallback(
    () =>
      setParams(
        (currentParams) => {
          const next = new URLSearchParams(currentParams);
          next.delete('open');
          return next;
        },
        { replace: true },
      ),
    [setParams],
  );

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <Typography.Title tag="h1" view="medium">
          Новости для бизнеса
        </Typography.Title>
        <label className={styles.role}>
          <span className={styles.roleLabel}>Должность</span>
          <select
            value={role ?? ''}
            onChange={(event) => setRole(isRole(event.target.value) ? event.target.value : null)}
          >
            <option value="">Вся компания</option>
            {Object.entries(ROLE_TITLES).map(([key, title]) => (
              <option key={key} value={key}>
                {title}
              </option>
            ))}
          </select>
        </label>
      </header>
      <div className={styles.tabs} role="tablist" aria-label="Источник событий">
        <button
          type="button"
          role="tab"
          aria-selected={feedFilters.filters.view === 'feed'}
          className={styles.tab}
          onClick={() => feedFilters.setView('feed')}
        >
          Для вашей компании
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={feedFilters.filters.view === 'related'}
          className={styles.tab}
          onClick={() => feedFilters.setView('related')}
        >
          Конкуренты и контрагенты
        </button>
      </div>
      {feedFilters.filters.view === 'related' ? (
        <RelatedList company={company} />
      ) : (
        <FeedView
          key={`${company}:${role ?? ''}`}
          company={company}
          role={role}
          login={user.login}
          openId={openId}
          onOpened={onOpened}
          feedFilters={feedFilters}
        />
      )}
    </div>
  );
}

type FeedViewProps = {
  company: string;
  role: Role | null;
  login: string;
  openId: string | null;
  onOpened: () => void;
  feedFilters: ReturnType<typeof useFeedFilters>;
};

function FeedView({ company, role, login, openId, onOpened, feedFilters }: FeedViewProps) {
  const { filters } = feedFilters;
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const controller = useRef<AbortController | null>(null);
  const panel = useRef<HTMLDivElement>(null);
  const seen = useSeen(login, company);
  const [seenOnOpen] = useState(seen);

  useEffect(() => () => controller.current?.abort(), []);

  const feed = useCachedApi<Feed>(
    feedPath(company, role, { days: filters.days, all: filters.all }),
  );
  const feedData = feed.data ?? (feed.loading ? feed.previous : undefined);
  const refreshing = feed.loading && feedData !== undefined;
  const items = feedData?.items ?? [];
  const visible = applyFilters(items, filters);
  const opened = items.find((item) => item.news_id === analysis?.newsId);

  const runInsight = useCallback(
    (newsId: string, body: InsightRequest) => {
      controller.current?.abort();
      const current = new AbortController();
      controller.current = current;
      setAnalysis({ newsId, busy: true });
      reveal(panel.current);
      apiPost<GeneratedInsight>(
        `/companies/${encodeURIComponent(company)}/insights`,
        body,
        current.signal,
      ).then(
        (insight) => setAnalysis({ newsId, insight, busy: false }),
        (error: unknown) => {
          if (!isAbort(error)) setAnalysis({ newsId, busy: false, error: errorMessage(error) });
        },
      );
    },
    [company],
  );

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
    if (feedData.items?.some((item) => item.news_id === openId)) {
      runInsight(openId, {
        news_ids: [openId],
        role,
        feed_snapshot_id: feedData.snapshot_id,
        refresh: false,
      });
    }
  }, [openId, feedData, role, runInsight, onOpened]);

  const open = (item: FeedItem) =>
    runInsight(item.news_id, {
      news_ids: [item.news_id],
      role,
      feed_snapshot_id: feedData?.snapshot_id,
      refresh: false,
    });

  const showTrace = (insightId: string) => {
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    setAnalysis((state) => state && { ...state, busy: true, error: undefined });
    apiGet<AgentTrace>(`/insights/${encodeURIComponent(insightId)}/trace`, current.signal).then(
      (trace) => setAnalysis((state) => state && { ...state, trace, busy: false }),
      (error: unknown) => {
        if (!isAbort(error))
          setAnalysis((state) => state && { ...state, busy: false, error: errorMessage(error) });
      },
    );
  };

  const close = () => {
    controller.current?.abort();
    setAnalysis(null);
  };

  return (
    <>
      <FeedFiltersBar
        items={items}
        filters={filters}
        onPolarity={feedFilters.setPolarity}
        onMarker={feedFilters.setMarker}
        onImportant={feedFilters.setImportant}
        onDays={feedFilters.setDays}
      />
      <div className={styles.layout}>
        <div className={styles.main}>
          <div ref={panel} className={styles.panel} aria-live="polite">
            {analysis && (
              <InsightPanel
                analysis={analysis}
                title={opened?.card.title}
                onTrace={showTrace}
                onClose={close}
              />
            )}
          </div>
          {refreshing && <p className={styles.refreshing}>Обновляем ленту…</p>}
          {feedData?.verified === false && <UnverifiedNotice warnings={feedData.warnings ?? []} />}
          {feed.loading && !feedData && (
            <div className={styles.plate}>
              <p className={styles.hint}>
                Подбираем события для вашей компании. В первый раз это может занять пару минут.
              </p>
              <Skeleton lines={4} />
            </div>
          )}
          {feed.error && (
            <div className={styles.plate}>
              <ErrorState
                title="Не удалось загрузить новости"
                message={feed.error}
                onRetry={feed.retry}
              />
            </div>
          )}
          {feedData && (
            <FeedList
              feed={feedData}
              items={items}
              visible={visible}
              filters={filters}
              seen={seenOnOpen}
              activeId={analysis?.newsId}
              onOpen={open}
              onReset={feedFilters.reset}
              onShowAll={() => feedFilters.setAll(true)}
            />
          )}
        </div>
        {feedData && (
          <FeedOverview
            items={items.filter((item) => item.importance.level !== 'background')}
            period={periodTitle(filters.days)}
          />
        )}
      </div>
    </>
  );
}

function UnverifiedNotice({ warnings }: { warnings: string[] }) {
  const budget = warnings.some((warning) => warning.includes('бюджет'));
  return (
    <div className={styles.notice} role="status">
      <p className={styles.noticeTitle}>Лента собрана без проверки моделью</p>
      <p>
        События отобраны и оценены по правилам: важных может оказаться больше, чем на самом деле, а
        оценки влияния приблизительные.
        {budget && ' Дневной лимит запросов к модели исчерпан, он обновится в 3:00 МСК.'}
      </p>
    </div>
  );
}

type FeedListProps = {
  feed: Feed;
  items: FeedItem[];
  visible: FeedItem[];
  filters: FeedFilters;
  seen: ReadonlySet<string>;
  activeId?: string;
  onOpen: (item: FeedItem) => void;
  onReset: () => void;
  onShowAll: () => void;
};

function FeedList({
  feed,
  items,
  visible,
  filters,
  seen,
  activeId,
  onOpen,
  onReset,
  onShowAll,
}: FeedListProps) {
  const hidden = feed.hidden_below_threshold;
  const warnings = feed.warnings ?? [];

  if (items.length === 0) {
    return (
      <div className={styles.plate}>
        <p>За выбранный период значимых для компании событий не нашлось.</p>
        <p className={styles.hint}>Попробуйте период побольше или другую должность.</p>
        {hidden > 0 && !filters.all && (
          <button type="button" className={styles.more} onClick={onShowAll}>
            Показать {hidden} малозначимых
          </button>
        )}
      </div>
    );
  }

  return (
    <>
      {visible.length === 0 ? (
        <div className={styles.plate}>
          <p>Под выбранные фильтры событий нет.</p>
          <button type="button" className={styles.more} onClick={onReset}>
            Сбросить фильтры
          </button>
        </div>
      ) : (
        <ul className={styles.list}>
          {visible.map((item) => (
            <li key={item.news_id}>
              <FeedCard
                item={item}
                feed={feed}
                isFresh={!seen.has(item.news_id)}
                active={item.news_id === activeId}
                onOpen={onOpen}
              />
            </li>
          ))}
        </ul>
      )}
      <div className={styles.footer}>
        {filters.all || hidden === 0 ? (
          <p>Показаны все события за период</p>
        ) : (
          <p>
            Скрыто {hidden}{' '}
            {plural(hidden, { one: 'малозначимое', few: 'малозначимых', many: 'малозначимых' })}{' '}
            <button type="button" className={styles.more} onClick={onShowAll}>
              Показать
            </button>
          </p>
        )}
        {warnings.length > 0 && (
          <p className={styles.warning}>Часть данных недоступна: {warnings.join('; ')}</p>
        )}
      </div>
    </>
  );
}
