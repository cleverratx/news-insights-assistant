import { ArrowDownSIcon } from '@alfalab/icons-glyph/ArrowDownSIcon';
import { ArrowUpSIcon } from '@alfalab/icons-glyph/ArrowUpSIcon';

import type { Feed, FeedItem } from '@/shared/api/types';
import { formatSigned } from '@/shared/lib/format';

import styles from './ImpactMeter.module.css';

type Props = {
  impact: FeedItem['impact'];
  bands?: Feed['impact_scale'];
  size?: 'l' | 'm';
};

export function ImpactMeter({ impact, bands, size = 'm' }: Props) {
  const score = Math.min(Math.max(impact.score, -100), 100);
  const band = bands?.find((item) => item.color === impact.color);
  const color = band?.hex;
  const uncertain = impact.confidence === 'low';
  const Arrow = score < 0 ? ArrowDownSIcon : score > 0 ? ArrowUpSIcon : null;
  const label = `Влияние ${formatSigned(score)} по шкале от −100 до +100: ${impact.title.toLowerCase()}${
    uncertain ? ', оценка неуверенная' : ''
  }`;

  return (
    <span
      className={`${styles.meter} ${styles[size]} ${uncertain ? styles.uncertain : ''}`}
      role="img"
      aria-label={label}
      title={uncertain ? `${impact.title}, оценка неуверенная` : impact.title}
    >
      <span className={styles.value} aria-hidden="true">
        {Arrow && <Arrow className={styles.arrow} style={color ? { color } : undefined} />}
        {formatSigned(score)}
      </span>
      <span className={styles.track} aria-hidden="true">
        <span
          className={styles.fill}
          style={{
            left: `${50 + Math.min(score, 0) / 2}%`,
            width: `${Math.abs(score) / 2}%`,
            background: color,
          }}
        />
        <span className={styles.zero} />
      </span>
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
