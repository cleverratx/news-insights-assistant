import { Link } from 'react-router';
import { ArrowDownSIcon } from '@alfalab/icons-glyph/ArrowDownSIcon';
import { ArrowUpSIcon } from '@alfalab/icons-glyph/ArrowUpSIcon';
import { ChevronRightMIcon } from '@alfalab/icons-glyph/ChevronRightMIcon';

import { isImportant } from '@/shared/api/feed';
import type { Feed, FeedItem } from '@/shared/api/types';
import type { ApiState } from '@/shared/api/useApi';
import { plural } from '@/shared/lib/format';
import { MARKER_TITLES } from '@/shared/lib/markers';
import { ImpactMeter } from '@/shared/ui/ImpactMeter';
import { ImportanceRing } from '@/shared/ui/ImportanceRing';
import { ErrorState, Skeleton } from '@/shared/ui/StateView';

import styles from './NewsWidget.module.css';

type Props = {
  feed: ApiState<Feed>;
  fresh: FeedItem[];
  search: string;
};

const LIST_SIZE = 4;

function feedLink(search: string, extra: Record<string, string> = {}) {
  const params = new URLSearchParams(search);
  Object.entries(extra).forEach(([key, value]) => params.set(key, value));
  return { pathname: '/feed', search: `?${params}` };
}

function analyzeLink(search: string, newsId: string) {
  return feedLink(search, { open: newsId });
}

export function NewsWidget({ feed, fresh, search }: Props) {
  const data = feed.data;
  const items = data?.items ?? [];
  const [lead, ...rest] = items;
  const freshIds = new Set(fresh.map((item) => item.news_id));

  return (
    <section className={styles.widget} aria-labelledby="news-widget-title">
      <header className={styles.head}>
        <div className={styles.heading}>
          <h2 id="news-widget-title" className={styles.title}>
            Новости для бизнеса
          </h2>
          {feed.data && <Summary items={items} fresh={fresh.length} search={search} />}
        </div>
        <Link className={styles.allButton} to={{ pathname: '/feed', search }}>
          Вся лента
        </Link>
      </header>

      <div aria-live="polite">
        {feed.loading && (
          <div className={styles.state}>
            <p className={styles.hint}>
              Подбираем события для вашей компании. В первый раз это может занять пару минут.
            </p>
            <Skeleton lines={4} />
          </div>
        )}
        {feed.error && (
          <div className={styles.state}>
            <ErrorState
              title="Не удалось загрузить новости"
              message={feed.error}
              onRetry={feed.retry}
            />
          </div>
        )}
        {feed.data && !lead && (
          <div className={styles.state}>
            <p>За неделю значимых для компании событий не нашлось.</p>
            <Link to={{ pathname: '/feed', search }}>Посмотреть все новости</Link>
          </div>
        )}
      </div>

      {data && lead && (
        <ul className={styles.list}>
          <li>
            <NewsRow
              item={lead}
              feed={data}
              isFresh={freshIds.has(lead.news_id)}
              to={analyzeLink(search, lead.news_id)}
              lead
            />
          </li>
          {rest.slice(0, LIST_SIZE).map((item) => (
            <li key={item.news_id}>
              <NewsRow
                item={item}
                feed={data}
                isFresh={freshIds.has(item.news_id)}
                to={analyzeLink(search, item.news_id)}
              />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

type SummaryProps = { items: FeedItem[]; fresh: number; search: string };

function Summary({ items, fresh, search }: SummaryProps) {
  const important = items.filter(isImportant).length;
  const risks = items.filter((item) => item.event.polarity === 'risk').length;
  const chances = items.filter((item) => item.event.polarity === 'opportunity').length;

  return (
    <ul className={styles.summary}>
      {fresh > 0 && (
        <li>
          <Link className={`${styles.chip} ${styles.chipNew}`} to={feedLink(search)}>
            {fresh} {plural(fresh, { one: 'новое', few: 'новых', many: 'новых' })}
          </Link>
        </li>
      )}
      {important > 0 && (
        <li>
          <Link
            className={`${styles.chip} ${styles.chipImportant}`}
            to={feedLink(search, { important: '1' })}
          >
            {important} {plural(important, { one: 'важное', few: 'важных', many: 'важных' })}
          </Link>
        </li>
      )}
      {risks > 0 && (
        <li>
          <Link
            className={`${styles.chip} ${styles.chipRisk}`}
            to={feedLink(search, { polarity: 'risk' })}
          >
            <ArrowDownSIcon className={styles.chipIcon} aria-hidden="true" />
            {risks} {plural(risks, { one: 'риск', few: 'риска', many: 'рисков' })}
          </Link>
        </li>
      )}
      {chances > 0 && (
        <li>
          <Link
            className={`${styles.chip} ${styles.chipChance}`}
            to={feedLink(search, { polarity: 'opportunity' })}
          >
            <ArrowUpSIcon className={styles.chipIcon} aria-hidden="true" />
            {chances}{' '}
            {plural(chances, { one: 'возможность', few: 'возможности', many: 'возможностей' })}
          </Link>
        </li>
      )}
      {fresh === 0 && <li className={styles.calm}>Новых важных событий нет</li>}
    </ul>
  );
}

type RowProps = {
  item: FeedItem;
  feed: Feed;
  isFresh: boolean;
  to: { pathname: string; search: string };
  lead?: boolean;
};

function NewsRow({ item, feed, isFresh, to, lead = false }: RowProps) {
  const markers = item.event.markers ?? [];
  const size = lead ? 'l' : 'm';

  return (
    <Link className={`${styles.row} ${lead ? styles.lead : ''}`} to={to}>
      <ImportanceRing importance={item.importance} scale={feed.importance_scale} size={size} />
      <span className={styles.body}>
        <span className={styles.newsTitle}>
          {isFresh && (
            <span className={styles.newDot}>
              <span className={styles.srOnly}>Новое: </span>
            </span>
          )}
          {item.card.title}
        </span>
        <span className={styles.marks}>
          {item.kind === 'bank_analytics' && (
            <span className={`${styles.mark} ${styles.markBank}`}>Аналитика Альфа-Банка</span>
          )}
          {markers.map((marker) => (
            <span key={marker} className={styles.mark}>
              {MARKER_TITLES[marker]}
            </span>
          ))}
        </span>
      </span>
      <ImpactMeter impact={item.impact} size={size} />
      <ChevronRightMIcon className={styles.chevron} aria-hidden="true" />
    </Link>
  );
}
