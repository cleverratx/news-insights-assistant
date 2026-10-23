import { ArrowDownSIcon } from '@alfalab/icons-glyph/ArrowDownSIcon';
import { ArrowUpSIcon } from '@alfalab/icons-glyph/ArrowUpSIcon';

import { markerCounts, PERIODS, type FeedFilters, type Polarity } from '@/features/feed/filters';
import { isImportant } from '@/shared/api/feed';
import type { FeedItem } from '@/shared/api/types';
import { plural } from '@/shared/lib/format';
import { MARKER_TITLES, type Marker } from '@/shared/lib/markers';

import styles from './FeedFilters.module.css';

type Props = {
  items: FeedItem[];
  filters: FeedFilters;
  onPolarity: (polarity: Polarity | null) => void;
  onMarker: (marker: Marker | null) => void;
  onImportant: (important: boolean) => void;
  onDays: (days: number) => void;
};

function periodTitle(days: number): string {
  if (days === 1) return 'За сутки';
  return `За ${days} ${plural(days, { one: 'день', few: 'дня', many: 'дней' })}`;
}

export function FeedFiltersBar({
  items,
  filters,
  onPolarity,
  onMarker,
  onImportant,
  onDays,
}: Props) {
  const risks = items.filter((item) => item.event.polarity === 'risk').length;
  const chances = items.filter((item) => item.event.polarity === 'opportunity').length;
  const important = items.filter(isImportant).length;
  const markers = markerCounts(items);

  return (
    <div className={styles.filters}>
      <div className={styles.row}>
        <div className={styles.segments} role="group" aria-label="Риски и возможности">
          <button
            type="button"
            className={styles.segment}
            aria-pressed={filters.polarity === null}
            onClick={() => onPolarity(null)}
          >
            Все <span className={styles.count}>{items.length}</span>
          </button>
          <button
            type="button"
            className={styles.segment}
            aria-pressed={filters.polarity === 'risk'}
            onClick={() => onPolarity(filters.polarity === 'risk' ? null : 'risk')}
          >
            <ArrowDownSIcon className={`${styles.icon} ${styles.risk}`} aria-hidden="true" />
            Риски <span className={styles.count}>{risks}</span>
          </button>
          <button
            type="button"
            className={styles.segment}
            aria-pressed={filters.polarity === 'opportunity'}
            onClick={() => onPolarity(filters.polarity === 'opportunity' ? null : 'opportunity')}
          >
            <ArrowUpSIcon className={`${styles.icon} ${styles.chance}`} aria-hidden="true" />
            Возможности <span className={styles.count}>{chances}</span>
          </button>
        </div>
        <button
          type="button"
          className={styles.toggle}
          aria-pressed={filters.important}
          onClick={() => onImportant(!filters.important)}
        >
          Только важные <span className={styles.count}>{important}</span>
        </button>
        <label className={styles.period}>
          <span className={styles.srOnly}>Период</span>
          <select value={filters.days} onChange={(event) => onDays(Number(event.target.value))}>
            {PERIODS.map((days) => (
              <option key={days} value={days}>
                {periodTitle(days)}
              </option>
            ))}
          </select>
        </label>
      </div>
      {markers.length > 0 && (
        <div className={styles.markers} role="group" aria-label="Темы">
          <button
            type="button"
            className={styles.marker}
            aria-pressed={filters.marker === null}
            onClick={() => onMarker(null)}
          >
            Все темы
          </button>
          {markers.map(([marker, count]) => (
            <button
              key={marker}
              type="button"
              className={styles.marker}
              aria-pressed={filters.marker === marker}
              onClick={() => onMarker(filters.marker === marker ? null : marker)}
            >
              {MARKER_TITLES[marker]} <span className={styles.count}>{count}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
