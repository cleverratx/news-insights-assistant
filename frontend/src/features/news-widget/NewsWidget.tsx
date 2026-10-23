import { useState } from 'react';
import { Link } from 'react-router';

import { isImportant } from '@/shared/api/feed';
import type { Feed, FeedItem } from '@/shared/api/types';
import type { ApiState } from '@/shared/api/useApi';
import { formatAgo, formatScore, formatSigned, plural } from '@/shared/lib/format';
import { ErrorState, Skeleton } from '@/shared/ui/StateView';

import styles from './NewsWidget.module.css';

type Props = {
  feed: ApiState<Feed>;
  fresh: FeedItem[];
  search: string;
};

const LEVEL_CLASS: Record<FeedItem['importance']['level'], string> = {
  critical: styles.levelCritical,
  important: styles.levelImportant,
  info: styles.levelInfo,
  background: styles.levelBackground,
};

function analyzeLink(search: string, newsId: string) {
  const params = new URLSearchParams(search);
  params.set('open', newsId);
  return { pathname: '/feed', search: `?${params}` };
}

function pickLead(items: FeedItem[], fresh: FeedItem[]): FeedItem | undefined {
  return fresh[0] ?? items[0];
}

export function NewsWidget({ feed, fresh, search }: Props) {
  const items = feed.data?.items ?? [];
  const lead = pickLead(items, fresh);
  const important = items.filter(isImportant);
  const others = (important.length > 1 ? important : items)
    .filter((item) => item !== lead)
    .slice(0, 3);
  const freshIds = new Set(fresh.map((item) => item.news_id));

  return (
    <section className={styles.widget} aria-labelledby="news-widget-title">
      <header className={styles.head}>
        <div className={styles.heading}>
          <h2 id="news-widget-title" className={styles.title}>
            Новости для бизнеса
          </h2>
          {feed.data &&
            (fresh.length > 0 ? (
              <span className={styles.counter}>
                {fresh.length}{' '}
                {plural(fresh.length, {
                  one: 'новое важное',
                  few: 'новых важных',
                  many: 'новых важных',
                })}
              </span>
            ) : (
              <span className={styles.calm}>Новых важных событий нет</span>
            ))}
        </div>
        <Link className={styles.allLink} to={{ pathname: '/feed', search }}>
          Все новости{feed.data ? ` · ${items.length}` : ''}
        </Link>
      </header>

      <div aria-live="polite">
        {feed.loading && (
          <div className={styles.loading}>
            <p className={styles.hint}>
              Подбираем события для вашей компании. В первый раз это может занять пару минут.
            </p>
            <Skeleton lines={4} />
          </div>
        )}
        {feed.error && (
          <ErrorState
            title="Не удалось загрузить новости"
            message={feed.error}
            onRetry={feed.retry}
          />
        )}
        {feed.data && !lead && (
          <div className={styles.empty}>
            <p>За неделю значимых для компании событий не нашлось.</p>
            <Link to={{ pathname: '/feed', search }}>Посмотреть все новости</Link>
          </div>
        )}
        {feed.data && lead && (
          <LeadInsight
            item={lead}
            feed={feed.data}
            isFresh={freshIds.has(lead.news_id)}
            search={search}
          />
        )}
      </div>

      {feed.data && others.length > 0 && (
        <div className={styles.more}>
          <h3 className={styles.moreTitle}>Ещё для вас</h3>
          <ul className={styles.moreList}>
            {others.map((item) => (
              <li key={item.news_id}>
                <Link className={styles.moreItem} to={analyzeLink(search, item.news_id)}>
                  <span
                    className={`${styles.dot} ${LEVEL_CLASS[item.importance.level]}`}
                    aria-hidden="true"
                  />
                  <span className={styles.moreText}>{item.explanation || item.card.title}</span>
                  <span className={styles.moreMeta}>
                    {freshIds.has(item.news_id) && <span className={styles.newMark}>Новое</span>}
                    {formatAgo(item.card.published_at)}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

type LeadProps = { item: FeedItem; feed: Feed; isFresh: boolean; search: string };

function LeadInsight({ item, feed, isFresh, search }: LeadProps) {
  const band = feed.impact_scale?.find((scale) => scale.color === item.impact.color);
  const level = feed.importance_scale?.find((scale) => scale.level === item.importance.level);
  const { card } = item;

  return (
    <article className={styles.lead}>
      <div className={styles.leadBody}>
        <div className={styles.marks}>
          {isFresh && <span className={styles.newMark}>Новое</span>}
          <span className={styles.mark} title={level?.description ?? undefined}>
            <span
              className={`${styles.dot} ${LEVEL_CLASS[item.importance.level]}`}
              aria-hidden="true"
            />
            {item.importance.title} · {formatScore(item.importance.score)} из 10
          </span>
          <span className={styles.mark}>
            <span
              className={styles.dot}
              style={band ? { background: band.hex } : undefined}
              aria-hidden="true"
            />
            {item.impact.title} {formatSigned(item.impact.score)}
            {item.impact.confidence === 'low' && ' · оценка неуверенная'}
          </span>
          {item.kind === 'bank_analytics' && (
            <span className={`${styles.mark} ${styles.bankMark}`}>Аналитика Альфа-Банка</span>
          )}
        </div>
        <p className={styles.label}>Что это значит для вас</p>
        <p className={styles.meaning}>{item.explanation || item.impact.rationale}</p>
        <p className={styles.newsTitle}>{card.title}</p>
        <p className={styles.meta}>
          {card.source} · {formatAgo(card.published_at)}
          {item.coverage > 1 &&
            ` · пишут ${item.coverage} ${plural(item.coverage, {
              one: 'издание',
              few: 'издания',
              many: 'изданий',
            })}`}
        </p>
        <div className={styles.actions}>
          <Link className={styles.primary} to={analyzeLink(search, item.news_id)}>
            Разобрать событие
          </Link>
          {card.url && (
            <a className={styles.secondary} href={card.url} target="_blank" rel="noreferrer">
              Первоисточник
            </a>
          )}
        </div>
      </div>
      <Thumb item={item} />
    </article>
  );
}

function Thumb({ item }: { item: FeedItem }) {
  const [broken, setBroken] = useState(false);
  const { card } = item;
  if (card.image && !broken) {
    return (
      <img
        className={styles.thumb}
        src={card.image}
        alt=""
        width={200}
        height={150}
        onError={() => setBroken(true)}
      />
    );
  }
  return (
    <span
      className={`${styles.thumb} ${styles.placeholder}`}
      style={{ background: card.placeholder.color }}
      aria-hidden="true"
    >
      {card.placeholder.initials}
    </span>
  );
}
