import type { CSSProperties } from 'react';

import type { Feed, FeedItem } from '@/shared/api/types';
import { formatScore } from '@/shared/lib/format';

import styles from './ImportanceRing.module.css';

type Props = {
  importance: FeedItem['importance'];
  scale?: Feed['importance_scale'];
  size?: 'l' | 'm' | 's';
};

const RADIUS = 20;
const LENGTH = 2 * Math.PI * RADIUS;

export function ImportanceRing({ importance, scale, size = 'm' }: Props) {
  const share = Math.min(Math.max(importance.score / 10, 0), 1);
  const level = scale?.find((item) => item.level === importance.level);
  const score = formatScore(importance.score);

  return (
    <span
      className={`${styles.ring} ${styles[size]}`}
      style={{ '--share': share } as CSSProperties}
      role="img"
      aria-label={`Важность ${score} из 10, ${importance.title.toLowerCase()}`}
      title={level?.description ? `${importance.title}: ${level.description}` : importance.title}
    >
      <svg viewBox="0 0 48 48" aria-hidden="true">
        <circle className={styles.track} cx="24" cy="24" r={RADIUS} />
        <circle
          className={styles.value}
          cx="24"
          cy="24"
          r={RADIUS}
          strokeDasharray={`${share * LENGTH} ${LENGTH}`}
        />
      </svg>
      <span className={styles.score} aria-hidden="true">
        {score}
      </span>
    </span>
  );
}
