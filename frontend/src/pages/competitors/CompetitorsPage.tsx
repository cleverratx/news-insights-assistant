import { useState } from 'react';
import { Link, useSearchParams } from 'react-router';
import { ButtonDesktop } from '@alfalab/core-components-button/desktop';
import { Typography } from '@alfalab/core-components-typography';
import { ChevronDownMIcon } from '@alfalab/icons-glyph/ChevronDownMIcon';
import { ChevronRightMIcon } from '@alfalab/icons-glyph/ChevronRightMIcon';

import { useShell } from '@/app/shell-context';
import { PERIODS } from '@/features/feed/filters';
import { NewsNavigation } from '@/features/feed/NewsNavigation';
import { fullAnalysisLink } from '@/features/feed/navigation';
import { useApi } from '@/shared/api/useApi';
import type { RelatedFeed } from '@/shared/api/types';
import { RoleSelect } from '@/features/positions/RoleSelect';
import { formatAgo, plural } from '@/shared/lib/format';
import { EmptyState, ErrorState, Skeleton } from '@/shared/ui/StateView';

import { RelatedAnalysis } from './RelatedAnalysis';
import {
  mentionTitle,
  readRelatedFilters,
  relatedFeedPath,
  SCOPES,
  type RelatedEvent,
} from './model';
import styles from './CompetitorsPage.module.css';

export function CompetitorsPage() {
  const { company, role, search } = useShell();
  const [params, setParams] = useSearchParams();
  const { scope, days } = readRelatedFilters(params);
  const related = useApi<RelatedFeed>(relatedFeedPath(company, scope, days));
  const data = related.data;
  const items = data?.items ?? [];
  const update = (key: string, value: string) => {
    const next = new URLSearchParams(window.location.search);
    next.set(key, value);
    setParams(next, { preventScrollReset: true });
  };
  const context = new URLSearchParams(params);
  new URLSearchParams(search).forEach((value, key) => context.set(key, value));

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <div>
          <Typography.Title tag="h1" view="medium">
            Анализ конкурентов
          </Typography.Title>
          <p className={styles.lead}>
            Что происходит у конкурентов и контрагентов и как это касается вас.
          </p>
        </div>
        <RoleSelect />
      </header>
      <NewsNavigation search={`?${context}`} />
      <div className={styles.controls}>
        <div className={styles.scopes} role="group" aria-label="Компании в подборке">
          {Object.entries(SCOPES).map(([value, title]) => (
            <button
              key={value}
              type="button"
              aria-pressed={scope === value}
              onClick={() => update('scope', value)}
            >
              {title}
            </button>
          ))}
        </div>
        <label className={styles.select}>
          <span>Период</span>
          <select value={days} onChange={(event) => update('days', event.target.value)}>
            {PERIODS.map((period) => (
              <option key={period} value={period}>
                {period === 1 ? 'За сутки' : `За ${period} дней`}
              </option>
            ))}
          </select>
        </label>
      </div>
      {related.loading && (
        <div className={styles.card} role="status">
          <p>Собираем события конкурентов и контрагентов…</p>
          <Skeleton lines={3} />
        </div>
      )}
      {related.error && (
        <div className={styles.card}>
          <ErrorState
            title="Не удалось загрузить события"
            message={related.error}
            onRetry={related.retry}
          />
        </div>
      )}
      {data && (
        <>
          <div className={styles.results}>
            <p role="status">
              {items.length}{' '}
              {plural(items.length, { one: 'событие', few: 'события', many: 'событий' })} за
              выбранный период
            </p>
            <ButtonDesktop size={40} view="secondary" onClick={related.retry}>
              Обновить
            </ButtonDesktop>
          </div>
          {items.length === 0 ? (
            <div className={styles.card}>
              <EmptyState
                title="Событий за этот период не нашлось"
                message="Попробуйте увеличить период или посмотреть все компании."
                action={
                  scope !== 'all' || days !== 14
                    ? {
                        title: 'Все события за 14 дней',
                        onClick: () =>
                          setParams((current) => {
                            const next = new URLSearchParams(current);
                            next.set('scope', 'all');
                            next.set('days', '14');
                            return next;
                          }),
                      }
                    : undefined
                }
              />
            </div>
          ) : (
            <ul className={styles.list}>
              {items.map((event) => (
                <li key={`${company}:${role ?? 'all'}:${scope}:${days}:${event.news_id}`}>
                  <EventCard event={event} />
                </li>
              ))}
            </ul>
          )}
          {data.matched > items.length && (
            <p className={styles.hint}>
              Показано {items.length} из {data.matched} событий. Сократите период, чтобы увидеть
              нужные.
            </p>
          )}
          {data.warnings?.length || data.unnamed_companies > 0 ? (
            <details className={styles.warnings}>
              <summary>О подборке</summary>
              {data.unnamed_companies > 0 && (
                <p>
                  Компаний без публичного названия: {data.unnamed_companies}. Их события пока не
                  попали в подборку.
                </p>
              )}
              {data.warnings?.map((warning) => (
                <p key={warning}>{warning}</p>
              ))}
            </details>
          ) : null}
        </>
      )}
    </div>
  );
}

function EventCard({ event }: { event: RelatedEvent }) {
  const { company, role, search, feed } = useShell();
  const [selected, setSelected] = useState(event.companies[0].id);
  const [open, setOpen] = useState(false);
  const mention = event.companies.find((item) => item.id === selected) ?? event.companies[0];
  const fullAnalysis = fullAnalysisLink(search, event, feed.data);

  return (
    <article className={styles.card}>
      <div className={styles.companies}>
        {event.companies.map((item) => (
          <span key={item.id} className={styles.company}>
            <span className={styles.relation}>{mentionTitle(item)}</span>
            {item.name}
          </span>
        ))}
      </div>
      <h2 className={styles.eventTitle}>
        {event.card.url ? (
          <a className={styles.titleLink} href={event.card.url} target="_blank" rel="noreferrer">
            {event.card.title}
          </a>
        ) : (
          <Link className={styles.titleLink} to={fullAnalysis}>
            {event.card.title}
          </Link>
        )}
      </h2>
      {event.summary && <p className={styles.summary}>{event.summary}</p>}
      <div className={styles.meta}>
        <span>{event.card.source}</span>
        <time dateTime={event.card.published_at}>{formatAgo(event.card.published_at)}</time>
      </div>
      <details className={styles.evidence}>
        <summary>
          Почему событие здесь
          {event.publications.length > 1
            ? ` · ${event.publications.length} ${plural(event.publications.length, { one: 'публикация', few: 'публикации', many: 'публикаций' })}`
            : ''}
        </summary>
        {event.companies.map((item) => (
          <p key={item.id}>
            <strong>{item.name}:</strong> {item.evidence}
          </p>
        ))}
        {event.publications.length > 1 && (
          <ul>
            {event.publications.map((publication) => (
              <li key={publication.news_id}>
                {publication.url ? (
                  <a href={publication.url} target="_blank" rel="noreferrer">
                    {publication.source}: {publication.title}
                  </a>
                ) : (
                  publication.title
                )}
              </li>
            ))}
          </ul>
        )}
      </details>
      <div className={styles.analysisActions}>
        {event.companies.length > 1 && (
          <label className={styles.select}>
            <span>Компания для разбора</span>
            <select
              value={selected}
              onChange={(event) => {
                setSelected(event.target.value);
                setOpen(false);
              }}
            >
              {event.companies.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
        )}
        <div className={styles.eventActions}>
          <button
            className={styles.analyze}
            type="button"
            aria-expanded={open}
            onClick={() => setOpen(!open)}
          >
            {open ? 'Скрыть разбор' : 'Влияние на наш бизнес'}
            <ChevronDownMIcon aria-hidden="true" className={open ? styles.rotated : undefined} />
          </button>
          <Link className={styles.fullAnalysis} to={fullAnalysis}>
            Полный разбор новости
            <ChevronRightMIcon aria-hidden="true" />
          </Link>
        </div>
      </div>
      {open && <RelatedAnalysis key={mention.id} company={company} role={role} mention={mention} />}
    </article>
  );
}
