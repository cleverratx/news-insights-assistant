import { ArrowDownSIcon } from '@alfalab/icons-glyph/ArrowDownSIcon';
import { ArrowUpSIcon } from '@alfalab/icons-glyph/ArrowUpSIcon';

import type { FeedItem } from '@/shared/api/types';
import { formatSigned } from '@/shared/lib/format';

import styles from './ImpactMeter.module.css';

type Props = {
  impact: FeedItem['impact'];
  size?: 'l' | 'm' | 's';
};

export function ImpactMeter({ impact, size = 'm' }: Props) {
  const score = Math.min(Math.max(impact.score, -100), 100);
  const tone = score < 0 ? styles.negative : score > 0 ? styles.positive : '';
  const uncertain = impact.confidence === 'low';
  const Arrow = score < 0 ? ArrowDownSIcon : score > 0 ? ArrowUpSIcon : null;
  const label = `Влияние ${formatSigned(score)} по шкале от −100 до +100: ${impact.title.toLowerCase()}${
    uncertain ? ', оценка неуверенная' : ''
  }`;

  return (
    <span
      className={`${styles.meter} ${styles[size]} ${tone} ${uncertain ? styles.uncertain : ''}`}
      role="img"
      aria-label={label}
      title={uncertain ? `${impact.title}, оценка неуверенная` : impact.title}
    >
      <span className={styles.value} aria-hidden="true">
        {Arrow && <Arrow className={styles.arrow} />}
        {formatSigned(score)}
      </span>
      <span className={styles.track} aria-hidden="true">
        <span
          className={styles.fill}
          style={{
            left: `${50 + Math.min(score, 0) / 2}%`,
            width: `${Math.abs(score) / 2}%`,
          }}
        />
        <span className={styles.zero} />
      </span>
      {size === 's' && (
        <span className={styles.caption} aria-hidden="true">
          {uncertain ? 'оценка неточная' : impact.title}
        </span>
      )}
      {size === 'l' && (
        <span className={styles.scale} aria-hidden="true">
          <span>−100</span>
          <span>0</span>
          <span>+100</span>
        </span>
      )}
    </span>
  );
}
