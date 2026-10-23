import { ArrowDownSIcon } from '@alfalab/icons-glyph/ArrowDownSIcon';
import { ArrowUpSIcon } from '@alfalab/icons-glyph/ArrowUpSIcon';

import {
  applyFilters,
  hasActiveFilters,
  isMarker,
  isSignificance,
  PERIODS,
  SIGNIFICANCE_TITLES,
  type FeedFilters,
  type Polarity,
  type Significance,
} from '@/features/feed/filters';
import type { FeedItem } from '@/shared/api/types';
import { plural } from '@/shared/lib/format';
import { MARKER_TITLES, type Marker } from '@/shared/lib/markers';

import styles from './FeedFilters.module.css';

type Props = {
  items: FeedItem[];
  filters: FeedFilters;
  loading: boolean;
  onPolarity: (polarity: Polarity | null) => void;
  onMarker: (marker: Marker | null) => void;
  onSignificance: (significance: Significance | null) => void;
  onDays: (days: number) => void;
  onReset: () => void;
};

export function FeedFiltersBar({
  items,
  filters,
  loading,
  onPolarity,
  onMarker,
  onSignificance,
  onDays,
  onReset,
}: Props) {
  const candidates = applyFilters(items, { ...filters, polarity: null });
  const risks = candidates.filter((item) => item.event.polarity === 'risk').length;
  const chances = candidates.filter((item) => item.event.polarity === 'opportunity').length;
  const count = (value: number) => (loading ? '—' : value);

  return (
    <section className={styles.filters} aria-label="Фильтры новостей">
      <div className={styles.fields}>
        <label className={[styles.field, styles.eventType].join(' ')}>
          <span>Тип события</span>
          <select
            value={filters.marker ?? ''}
            onChange={(event) => onMarker(isMarker(event.target.value) ? event.target.value : null)}
          >
            <option value="">Все типы</option>
            {Object.entries(MARKER_TITLES).map(([value, title]) => (
              <option key={value} value={value}>
                {title}
              </option>
            ))}
          </select>
        </label>
        <label className={styles.field}>
          <span>Период</span>
          <select value={filters.days} onChange={(event) => onDays(Number(event.target.value))}>
            {PERIODS.map((days) => (
              <option key={days} value={days}>
                {days === 1
                  ? 'За сутки'
                  : 'За ' + days + ' ' + plural(days, { one: 'день', few: 'дня', many: 'дней' })}
              </option>
            ))}
          </select>
        </label>
        <label className={styles.field}>
          <span>Значимость</span>
          <select
            value={filters.significance ?? ''}
            onChange={(event) =>
              onSignificance(isSignificance(event.target.value) ? event.target.value : null)
            }
          >
            <option value="">Любая</option>
            {Object.entries(SIGNIFICANCE_TITLES).map(([value, title]) => (
              <option key={value} value={value}>
                {title}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className={styles.row}>
        <div className={styles.segments} role="group" aria-label="Влияние на компанию">
          <button
            type="button"
            className={styles.segment}
            aria-pressed={filters.polarity === null}
            onClick={() => onPolarity(null)}
          >
            Все <span className={styles.count}>{count(candidates.length)}</span>
          </button>
          <button
            type="button"
            className={styles.segment}
            aria-pressed={filters.polarity === 'risk'}
            onClick={() => onPolarity('risk')}
          >
            <ArrowDownSIcon className={[styles.icon, styles.risk].join(' ')} aria-hidden="true" />
            Только риски <span className={styles.count}>{count(risks)}</span>
          </button>
          <button
            type="button"
            className={styles.segment}
            aria-pressed={filters.polarity === 'opportunity'}
            onClick={() => onPolarity('opportunity')}
          >
            <ArrowUpSIcon className={[styles.icon, styles.chance].join(' ')} aria-hidden="true" />
            Возможности <span className={styles.count}>{count(chances)}</span>
          </button>
        </div>
        <button
          type="button"
          className={styles.reset}
          disabled={!hasActiveFilters(filters)}
          onClick={onReset}
        >
          Сбросить фильтры
        </button>
      </div>
    </section>
  );
}
