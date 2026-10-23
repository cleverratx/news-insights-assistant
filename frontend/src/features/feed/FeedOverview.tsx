import type { FeedItem } from '@/shared/api/types';
import { formatSigned, plural } from '@/shared/lib/format';
import { ImpactMeter } from '@/shared/ui/ImpactMeter';
import { ImportanceRing } from '@/shared/ui/ImportanceRing';

import styles from './FeedOverview.module.css';

type Props = { items: FeedItem[]; period: string };

const SAMPLE_RINGS: FeedItem['importance'][] = [
  { score: 3.2, level: 'info', title: 'К сведению', factors: [] },
  { score: 6, level: 'important', title: 'Важно', factors: [] },
  { score: 9, level: 'critical', title: 'Критично', factors: [] },
];

function sampleImpact(score: number, title: string): FeedItem['impact'] {
  return {
    score,
    title,
    color: score < 0 ? 'orange' : 'light_green',
    criteria: [],
    rationale: '',
    method: 'rules',
    confidence: 'high',
  };
}

function verdict(negative: number, positive: number): string {
  if (negative + positive === 0) return 'Заметного влияния на бизнес нет';
  const share = negative / (negative + positive);
  if (share >= 0.6) return 'Преобладают риски';
  if (share <= 0.4) return 'Преобладают возможности';
  return 'Риски и возможности уравновешены';
}

export function FeedOverview({ items, period }: Props) {
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
      </section>
      <section className={styles.plate} aria-labelledby="legend-title">
        <h2 id="legend-title" className={styles.title}>
          Обозначения
        </h2>
        <div className={styles.sample}>
          <span className={styles.rings}>
            {SAMPLE_RINGS.map((importance) => (
              <ImportanceRing key={importance.score} importance={importance} size="s" />
            ))}
          </span>
          <span>Важность для вас, от 0 до 10</span>
        </div>
        <div className={styles.sample}>
          <span className={styles.meter}>
            <ImpactMeter impact={sampleImpact(-30, 'Риск')} size="s" bare />
          </span>
          <span>Риск для бизнеса</span>
        </div>
        <div className={styles.sample}>
          <span className={styles.meter}>
            <ImpactMeter impact={sampleImpact(30, 'Возможность')} size="s" bare />
          </span>
          <span>Возможность для бизнеса</span>
        </div>
      </section>
    </aside>
  );
}
