import type { FeedItem } from '@/shared/api/types';
import { formatSigned, plural } from '@/shared/lib/format';

import styles from './FeedOverview.module.css';

type Props = { items: FeedItem[]; period: string };

function verdict(negative: number, positive: number): string {
  if (negative + positive === 0) return 'Заметного влияния на бизнес нет';
  const share = negative / (negative + positive);
  if (share >= 0.6) return 'Преобладают риски';
  if (share <= 0.4) return 'Преобладают возможности';
  return 'Риски и возможности уравновешены';
}

type Direction = { id: string; title: string; points: number; count: number };

function directions(items: FeedItem[]): Direction[] {
  const byId = new Map<string, Direction>();
  items.forEach((item) =>
    item.impact.criteria
      .filter((criterion) => criterion.level !== 0)
      .forEach((criterion) => {
        const current = byId.get(criterion.id) ?? {
          id: criterion.id,
          title: criterion.title,
          points: 0,
          count: 0,
        };
        byId.set(criterion.id, {
          ...current,
          points: current.points + criterion.points,
          count: current.count + 1,
        });
      }),
  );
  return [...byId.values()]
    .filter((direction) => direction.points !== 0)
    .sort((a, b) => Math.abs(b.points) - Math.abs(a.points))
    .slice(0, 5);
}

export function FeedOverview({ items, period }: Props) {
  const top = directions(items);
  const negatives = items.filter((item) => item.impact.score < 0);
  const positives = items.filter((item) => item.impact.score > 0);
  const negative = negatives.reduce((sum, item) => sum - item.impact.score, 0);
  const positive = positives.reduce((sum, item) => sum + item.impact.score, 0);
  const total = negative + positive;

  return (
    <aside className={styles.aside}>
      <section className={styles.plate} aria-labelledby="overview-title">
        <h2 id="overview-title" className={styles.title}>
          Влияние на бизнес
        </h2>
        <p className={styles.caption}>{period}, по всем событиям ленты</p>
        <p className={styles.verdict}>{verdict(negative, positive)}</p>
        <div
          className={styles.balance}
          role="img"
          aria-label={`Сумма оценок рисков ${formatSigned(-negative)}, возможностей ${formatSigned(positive)}`}
        >
          {total > 0 ? (
            <>
              {negative > 0 && <span className={styles.negative} style={{ flexGrow: negative }} />}
              {positive > 0 && <span className={styles.positive} style={{ flexGrow: positive }} />}
            </>
          ) : (
            <span className={styles.empty} />
          )}
        </div>
        <ul className={styles.legend}>
          <li>
            <span className={`${styles.dot} ${styles.negative}`} aria-hidden="true" />
            {negatives.length}{' '}
            {plural(negatives.length, { one: 'риск', few: 'риска', many: 'рисков' })}
            <span className={styles.sum}>{formatSigned(-negative)}</span>
          </li>
          <li>
            <span className={`${styles.dot} ${styles.positive}`} aria-hidden="true" />
            {positives.length}{' '}
            {plural(positives.length, {
              one: 'возможность',
              few: 'возможности',
              many: 'возможностей',
            })}
            <span className={styles.sum}>{formatSigned(positive)}</span>
          </li>
        </ul>
        {top.length > 0 && (
          <div className={styles.directions}>
            <h3 className={styles.subtitle}>Что затронуто сильнее всего</h3>
            <ul className={styles.directionList}>
              {top.map((direction) => (
                <li key={direction.id}>
                  <span
                    className={`${styles.arrow} ${direction.points < 0 ? styles.down : styles.up}`}
                    aria-hidden="true"
                  >
                    {direction.points < 0 ? '↓' : '↑'}
                  </span>
                  <span className={styles.directionTitle}>
                    {direction.title}
                    <span className={styles.count}>
                      {' '}
                      · {direction.count}{' '}
                      {plural(direction.count, { one: 'событие', few: 'события', many: 'событий' })}
                    </span>
                  </span>
                  <span className={styles.sum}>{formatSigned(direction.points)}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>
    </aside>
  );
}
