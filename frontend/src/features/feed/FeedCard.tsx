import { useState } from 'react';

import type { Feed, FeedItem } from '@/shared/api/types';
import { formatAgo, plural } from '@/shared/lib/format';
import { MARKER_TITLES } from '@/shared/lib/markers';
import { ImpactMeter } from '@/shared/ui/ImpactMeter';
import { ImportanceRing } from '@/shared/ui/ImportanceRing';

import styles from './FeedCard.module.css';

type Props = {
  item: FeedItem;
  feed: Feed;
  isFresh: boolean;
  active: boolean;
  onOpen: (item: FeedItem) => void;
};

export function FeedCard({ item, feed, isFresh, active, onOpen }: Props) {
  const { card, event } = item;
  const markers = event.markers ?? [];

  return (
    <article className={`${styles.card} ${active ? styles.active : ''}`}>
      <div className={styles.scores}>
        <ImportanceRing importance={item.importance} scale={feed.importance_scale} />
        <ImpactMeter impact={item.impact} size="s" />
      </div>
      <div className={styles.body}>
        <p className={styles.meta}>
          <span className={styles.level}>{item.importance.title}</span>
          <span>{event.type}</span>
          <time dateTime={card.published_at}>{formatAgo(card.published_at)}</time>
        </p>
        <h2 className={styles.title}>
          <button type="button" className={styles.open} onClick={() => onOpen(item)}>
            {isFresh && (
              <span className={styles.newDot}>
                <span className={styles.srOnly}>Новое: </span>
              </span>
            )}
            {card.title}
          </button>
        </h2>
        {item.explanation && (
          <p className={styles.reason}>
            <span className={styles.reasonLabel}>Для вас: </span>
            {item.explanation}
          </p>
        )}
        <div className={styles.footer}>
          {item.kind === 'bank_analytics' && (
            <span className={`${styles.chip} ${styles.bank}`}>Аналитика Альфа-Банка</span>
          )}
          {markers.map((marker) => (
            <span key={marker} className={styles.chip}>
              {MARKER_TITLES[marker]}
            </span>
          ))}
          <span className={styles.source}>
            {card.url ? (
              <a className={styles.sourceLink} href={card.url} target="_blank" rel="noreferrer">
                {card.source}
              </a>
            ) : (
              card.source
            )}
            {item.coverage > 1 &&
              ` · пишут ${item.coverage} ${plural(item.coverage, {
                one: 'издание',
                few: 'издания',
                many: 'изданий',
              })}`}
          </span>
        </div>
      </div>
      <Thumb image={card.image} />
    </article>
  );
}

function Thumb({ image }: { image?: string | null }) {
  const [broken, setBroken] = useState(false);
  if (!image || broken) return null;
  return (
    <img
      className={styles.thumb}
      src={image}
      alt=""
      width={136}
      height={102}
      loading="lazy"
      onError={() => setBroken(true)}
    />
  );
}
