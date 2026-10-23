import { useEffect, useState } from 'react';
import { Typography } from '@alfalab/core-components-typography';

import { useShell } from '@/app/shell-context';
import { FeedCard } from '@/features/feed/FeedCard';
import { FeedFiltersBar } from '@/features/feed/FeedFilters';
import { FeedOverview } from '@/features/feed/FeedOverview';
import { applyFilters, useFeedFilters, type FeedFilters } from '@/features/feed/filters';
import { RelatedList } from '@/features/feed/RelatedList';
import { newsLink } from '@/features/insight/link';
import { useCachedApi } from '@/shared/api/cache';
import { feedPath } from '@/shared/api/feed';
import type { Feed, FeedItem } from '@/shared/api/types';
import { isRole, ROLE_TITLES, type Role } from '@/shared/lib/demo';
import { plural } from '@/shared/lib/format';
import { markSeen, useSeen } from '@/shared/lib/seen';
import { ErrorState, Skeleton } from '@/shared/ui/StateView';

import styles from './FeedPage.module.css';

function periodTitle(days: number): string {
  if (days === 1) return 'За сутки';
  return `За ${days} ${plural(days, { one: 'день', few: 'дня', many: 'дней' })}`;
}

export function FeedPage() {
  const { company, role, setRole, user, search } = useShell();
  const feedFilters = useFeedFilters();

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
          search={search}
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
  search: string;
  feedFilters: ReturnType<typeof useFeedFilters>;
};

function FeedView({ company, role, login, search, feedFilters }: FeedViewProps) {
  const { filters } = feedFilters;
  const seen = useSeen(login, company);
  const [seenOnOpen] = useState(seen);

  const feed = useCachedApi<Feed>(feedPath(company, role, { days: filters.days }));
  const extra = useCachedApi<Feed>(
    filters.all ? feedPath(company, role, { days: filters.days, all: true }) : null,
  );
  const feedData = feed.data ?? (feed.loading ? feed.previous : undefined);
  const refreshing = feed.loading && feedData !== undefined;
  const baseItems = feedData?.items ?? [];
  const baseIds = new Set(baseItems.map((item) => item.news_id));
  const extraItems = (extra.data?.items ?? []).filter((item) => !baseIds.has(item.news_id));
  const items = [...baseItems, ...extraItems];
  const visible = applyFilters(items, filters);
  const linkFor = (item: FeedItem) =>
    newsLink(
      search,
      item.news_id,
      baseIds.has(item.news_id) ? feedData?.snapshot_id : extra.data?.snapshot_id,
    );

  useEffect(() => {
    if (feedData)
      markSeen(
        login,
        company,
        (feedData.items ?? []).map((item) => item.news_id),
      );
  }, [feedData, login, company]);

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
          {refreshing && <p className={styles.refreshing}>Обновляем ленту…</p>}
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
              extraLoading={extra.loading}
              seen={seenOnOpen}
              linkFor={linkFor}
              onReset={feedFilters.reset}
              onShowAll={() => feedFilters.setAll(true)}
            />
          )}
        </div>
        {feedData && <FeedOverview items={baseItems} period={periodTitle(filters.days)} />}
      </div>
    </>
  );
}

type FeedListProps = {
  feed: Feed;
  items: FeedItem[];
  visible: FeedItem[];
  filters: FeedFilters;
  extraLoading: boolean;
  seen: ReadonlySet<string>;
  linkFor: (item: FeedItem) => { pathname: string; search: string };
  onReset: () => void;
  onShowAll: () => void;
};

function FeedList({
  feed,
  items,
  visible,
  filters,
  extraLoading,
  seen,
  linkFor,
  onReset,
  onShowAll,
}: FeedListProps) {
  const hidden = feed.hidden_below_threshold;

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
                to={linkFor(item)}
              />
            </li>
          ))}
        </ul>
      )}
      <div className={styles.footer} aria-live="polite">
        {filters.all && extraLoading ? (
          <p>Загружаем малозначимые события…</p>
        ) : filters.all || hidden === 0 ? (
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
      </div>
    </>
  );
}
