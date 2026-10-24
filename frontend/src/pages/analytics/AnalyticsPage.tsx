import { useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router';
import { ButtonDesktop } from '@alfalab/core-components-button/desktop';
import { Typography } from '@alfalab/core-components-typography';
import { ArrowDownSIcon } from '@alfalab/icons-glyph/ArrowDownSIcon';
import { ArrowUpSIcon } from '@alfalab/icons-glyph/ArrowUpSIcon';
import { BulbMIcon } from '@alfalab/icons-glyph/BulbMIcon';
import { ChartLineAscMIcon } from '@alfalab/icons-glyph/ChartLineAscMIcon';

import { useShell } from '@/app/shell-context';
import { NewsNavigation } from '@/features/feed/NewsNavigation';
import { PERIODS } from '@/features/feed/filters';
import { newsLink } from '@/features/insight/link';
import { useCachedApi } from '@/shared/api/cache';
import { FEED_LIMIT, feedPath } from '@/shared/api/feed';
import type { Feed } from '@/shared/api/types';
import { isRole, ROLE_TITLES } from '@/shared/lib/demo';
import { formatDateTime, plural } from '@/shared/lib/format';
import { EmptyState, ErrorState, Skeleton } from '@/shared/ui/StateView';

import { DailyImpact, DistributionChart, EventBalance, MiniBars } from './Charts';
import { PeriodDigest } from './PeriodDigest';
import {
  buildAnalytics,
  DIRECTION_TITLES,
  directionOf,
  selectEvents,
  type Selection,
} from './model';
import styles from './AnalyticsPage.module.css';

export function AnalyticsPage() {
  const { company, role, search } = useShell();
  const [params, setParams] = useSearchParams();
  const rawDays = Number(params.get('days'));
  const days = PERIODS.some((period) => period === rawDays) ? rawDays : 7;
  const feed = useCachedApi<Feed>(feedPath(company, role, { days, all: true, limit: FEED_LIMIT }));
  const context = new URLSearchParams(search);
  context.set('days', String(days));
  const contextSearch = `?${context}`;
  const update = (key: string, value: string) => {
    const next = new URLSearchParams(window.location.search);
    next.set(key, value);
    setParams(next, { preventScrollReset: true });
  };

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <div>
          <Typography.Title tag="h1" view="medium">
            Аналитика
          </Typography.Title>
          <p className={styles.lead}>
            Что меняется вокруг бизнеса и какие события требуют внимания.
          </p>
        </div>
        <label className={styles.role}>
          <span>Разрез аналитики</span>
          <select
            value={role ?? ''}
            onChange={(event) =>
              update('role', isRole(event.target.value) ? event.target.value : 'all')
            }
          >
            <option value="">Вся компания</option>
            {Object.entries(ROLE_TITLES).map(([value, title]) => (
              <option key={value} value={value}>
                {title}
              </option>
            ))}
          </select>
        </label>
      </header>
      <NewsNavigation search={contextSearch} />
      <div className={styles.toolbar}>
        <div className={styles.periods} role="group" aria-label="Период аналитики">
          {PERIODS.map((period) => (
            <button
              type="button"
              key={period}
              aria-pressed={days === period}
              onClick={() => update('days', String(period))}
            >
              {period} {plural(period, { one: 'день', few: 'дня', many: 'дней' })}
            </button>
          ))}
        </div>
        <div className={styles.updated}>
          {feed.data && <span>Обновлено {formatDateTime(feed.data.generated_at)}</span>}
          <ButtonDesktop size={40} view="secondary" onClick={feed.retry} disabled={feed.loading}>
            Обновить
          </ButtonDesktop>
        </div>
      </div>
      {feed.loading && (
        <div className={styles.loading} role="status">
          <p>Рассчитываем аналитику по вашей ленте…</p>
          <div className={styles.metrics}>
            {[0, 1, 2, 3].map((item) => (
              <div className={styles.panel} key={item}>
                <Skeleton lines={2} />
              </div>
            ))}
          </div>
          <div className={styles.panel}>
            <Skeleton lines={5} />
          </div>
        </div>
      )}
      {feed.error && (
        <div className={styles.panel}>
          <ErrorState
            title="Не удалось загрузить аналитику"
            message={feed.error}
            onRetry={feed.retry}
          />
        </div>
      )}
      {feed.data && (
        <Dashboard
          key={`${company}:${role ?? 'all'}:${days}`}
          feed={feed.data}
          search={contextSearch}
        />
      )}
    </div>
  );
}

function Dashboard({ feed, search }: { feed: Feed; search: string }) {
  const data = useMemo(() => buildAnalytics(feed), [feed]);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [limit, setLimit] = useState(6);
  const select = (next: Selection) => {
    setSelection(selection?.kind === next.kind && selection.key === next.key ? null : next);
    setLimit(6);
  };
  const selected = selectEvents(data.items, selection);
  const total = data.items.length;
  const share = (count: number) => (total ? Math.round((count / total) * 100) : 0);
  const charts = { data, selection, onSelect: select };

  if (!total)
    return (
      <div className={styles.panel}>
        <EmptyState
          title="За этот период событий не нашлось"
          message="Выберите более длинный период или другой разрез аналитики."
        />
      </div>
    );

  return (
    <>
      <div className={styles.metrics}>
        <article className={styles.metric}>
          <div className={styles.metricLabel}>
            <ChartLineAscMIcon aria-hidden="true" />
            Событий в подборке
          </div>
          <div className={styles.metricValue}>
            {total}
            <MiniBars days={data.days} kind="count" />
          </div>
          <p>За выбранный период</p>
        </article>
        <article className={styles.metric} data-tone="attention">
          <div className={styles.metricLabel}>
            <BulbMIcon aria-hidden="true" />
            Требуют внимания
          </div>
          <div className={styles.metricValue}>
            {data.important}
            <span className={styles.metricPercent}>{share(data.important)}%</span>
          </div>
          <p>С высокой важностью для вас</p>
        </article>
        <article className={styles.metric} data-tone="risk">
          <div className={styles.metricLabel}>
            <ArrowDownSIcon aria-hidden="true" />С рисками
          </div>
          <div className={styles.metricValue}>
            {data.riskCount}
            <MiniBars days={data.days} kind="risk" />
          </div>
          <p>Включая двоякое влияние</p>
        </article>
        <article className={styles.metric} data-tone="opportunity">
          <div className={styles.metricLabel}>
            <ArrowUpSIcon aria-hidden="true" />С возможностями
          </div>
          <div className={styles.metricValue}>
            {data.opportunityCount}
            <MiniBars days={data.days} kind="opportunity" />
          </div>
          <p>Включая двоякое влияние</p>
        </article>
      </div>
      <div className={styles.chartGrid}>
        <DailyImpact {...charts} />
        <EventBalance {...charts} />
      </div>
      <div className={styles.twoColumns}>
        <DistributionChart
          title="Распределение по маркерам"
          description="Какие типы событий встречались в подборке"
          groups={data.markers}
          kind="marker"
          total={total}
          selection={selection}
          onSelect={select}
        />
        <DistributionChart
          title="О чём писали чаще всего"
          description="Темы, которые формировали повестку периода"
          groups={data.topics}
          kind="topic"
          total={total}
          selection={selection}
          onSelect={select}
        />
      </div>
      <PeriodDigest
        key={`${feed.company_id}:${feed.role ?? 'all'}:${feed.period_days}`}
        company={feed.company_id}
        role={feed.role ?? null}
        days={feed.period_days}
        feed={feed}
        search={search}
      />
      <section className={styles.panel} aria-labelledby="analytics-events-title">
        <div className={styles.panelHead}>
          <div>
            <h2 id="analytics-events-title">События за период</h2>
            <p className={styles.caption}>
              {selection ? selection.title : 'Материалы, на которых построены графики'} ·{' '}
              {selected.length}
            </p>
          </div>
          {selection && (
            <button
              className={styles.reset}
              type="button"
              onClick={() => {
                setSelection(null);
                setLimit(6);
              }}
            >
              Сбросить выбор ×
            </button>
          )}
        </div>
        {selected.length === 0 ? (
          <EmptyState
            title="Событий с таким условием нет"
            message="Выберите другой день, маркер или тему."
          />
        ) : (
          <ul className={styles.eventList}>
            {selected.slice(0, limit).map((item) => (
              <li key={item.news_id}>
                <span className={styles.eventDirection} data-direction={directionOf(item)}>
                  {DIRECTION_TITLES[directionOf(item)]}
                </span>
                <Link to={newsLink(search, item.news_id, feed.snapshot_id)}>{item.card.title}</Link>
                <span className={styles.eventMeta}>
                  {item.card.source} · {formatDateTime(item.card.published_at)}
                </span>
              </li>
            ))}
          </ul>
        )}
        {selected.length > limit && (
          <button className={styles.textButton} type="button" onClick={() => setLimit(limit + 6)}>
            Показать ещё {Math.min(6, selected.length - limit)}
          </button>
        )}
      </section>
      <details className={styles.calculation}>
        <summary>О расчёте показателей</summary>
        <p>
          В расчёте {total} {plural(total, { one: 'событие', few: 'события', many: 'событий' })}{' '}
          персональной ленты.{' '}
          {total >= FEED_LIMIT ? 'Показаны до 50 наиболее релевантных событий за период. ' : ''}
          Повторные публикации одного сюжета считаются одним событием.
        </p>
        <p>
          Даты публикации показаны по Москве. Первый и последний день могут быть неполными. Риски и
          возможности одного события учитываются отдельно. У события может быть несколько маркеров и
          тем, поэтому суммы распределений могут превышать число событий.
        </p>
        {data.units === 'events' && (
          <p>
            У событий разные шкалы оценки. На графике по дням показано количество событий, чтобы не
            складывать несопоставимые баллы.
          </p>
        )}
        {data.uncertain > 0 && (
          <p>
            Предварительных оценок с низкой уверенностью: {data.uncertain}. Основания можно
            посмотреть в полном разборе новости.
          </p>
        )}
        {feed.warnings?.map((warning) => (
          <p key={warning}>{warning}</p>
        ))}
      </details>
    </>
  );
}
