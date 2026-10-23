import { ArrowDownSIcon } from '@alfalab/icons-glyph/ArrowDownSIcon';
import { ArrowUpSIcon } from '@alfalab/icons-glyph/ArrowUpSIcon';

import type { FeedItem } from '@/shared/api/types';
import { formatSigned } from '@/shared/lib/format';

import styles from './ImpactCriteria.module.css';

type Criterion = FeedItem['impact']['criteria'][number];

const LEVEL_WORDS: Record<number, string> = {
  [-2]: 'существенно хуже',
  [-1]: 'заметно хуже',
  1: 'заметно лучше',
  2: 'существенно лучше',
};

const SOURCE_WORDS = { source: 'назван в материале', estimate: 'оценка ИИ' } as const;

export function affectedCriteria(impact: FeedItem['impact']): Criterion[] {
  return impact.criteria
    .filter((criterion) => criterion.level !== 0)
    .sort((a, b) => Math.abs(b.points) - Math.abs(a.points));
}

function levelWords(level: number): string {
  return LEVEL_WORDS[Math.max(-2, Math.min(2, level))] ?? '';
}

function Arrow({ level }: { level: number }) {
  const Icon = level < 0 ? ArrowDownSIcon : ArrowUpSIcon;
  return (
    <Icon className={`${styles.arrow} ${level < 0 ? styles.down : styles.up}`} aria-hidden="true" />
  );
}

export function CriteriaLine({
  impact,
  limit = 3,
}: {
  impact: FeedItem['impact'];
  limit?: number;
}) {
  const criteria = affectedCriteria(impact);
  if (criteria.length === 0) return null;
  const shown = criteria.slice(0, limit);

  return (
    <ul className={styles.line} aria-label="Как влияет на бизнес">
      {shown.map((criterion) => (
        <li key={criterion.id} className={styles.lineItem}>
          <Arrow level={criterion.level} />
          <span>
            <span className={styles.title}>{criterion.title}</span>
            <span className={criterion.level < 0 ? styles.downText : styles.upText}>
              {' '}
              — {levelWords(criterion.level)}
            </span>
            {criterion.timing_title && (
              <span className={styles.meta}> · {criterion.timing_title}</span>
            )}
            {criterion.timing_source && (
              <span className={styles.meta}> · {SOURCE_WORDS[criterion.timing_source]}</span>
            )}
          </span>
        </li>
      ))}
      {criteria.length > shown.length && (
        <li className={styles.more}>и ещё {criteria.length - shown.length}</li>
      )}
    </ul>
  );
}

export function CriteriaTable({ impact }: { impact: FeedItem['impact'] }) {
  const criteria = affectedCriteria(impact);

  return (
    <section className={styles.table} aria-labelledby="criteria-title">
      <div className={styles.tableHead}>
        <h2 id="criteria-title" className={styles.tableTitle}>
          Как скажется на бизнесе
        </h2>
        <span className={styles.total}>
          Итог {formatSigned(impact.score)} из ±100
          {impact.confidence === 'low' && ' · оценка неточная'}
        </span>
      </div>
      {criteria.length === 0 ? (
        <p className={styles.none}>{impact.rationale || 'Заметного влияния на показатели нет.'}</p>
      ) : (
        <ul className={styles.rows}>
          {criteria.map((criterion) => (
            <li key={criterion.id} className={styles.row}>
              <div className={styles.rowHead}>
                <Arrow level={criterion.level} />
                <span className={styles.rowTitle}>{criterion.title}</span>
                <span className={criterion.level < 0 ? styles.downText : styles.upText}>
                  {levelWords(criterion.level)}
                </span>
                <span className={styles.points}>{formatSigned(criterion.points)}</span>
              </div>
              {(criterion.timing_title || criterion.timing_source) && (
                <div className={styles.chips}>
                  {criterion.timing_title && (
                    <span className={styles.chip}>{criterion.timing_title}</span>
                  )}
                  {criterion.timing_source && (
                    <span
                      className={`${styles.chip} ${
                        criterion.timing_source === 'estimate' ? styles.estimate : styles.sourced
                      }`}
                    >
                      срок: {SOURCE_WORDS[criterion.timing_source]}
                    </span>
                  )}
                </div>
              )}
              {criterion.reason && <p className={styles.reason}>{criterion.reason}</p>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
