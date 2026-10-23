import { useSearchParams } from 'react-router';

import type { FeedItem } from '@/shared/api/types';
import { MARKER_TITLES, type Marker } from '@/shared/lib/markers';

export type Polarity = 'risk' | 'opportunity';
export type FeedView = 'feed' | 'related';
export type Significance = FeedItem['significance'];

export const PERIODS = [1, 3, 7, 14] as const;
export const DEFAULT_DAYS = 7;
export const SIGNIFICANCE_TITLES: Record<Significance, string> = {
  high: 'Высокая',
  medium: 'Средняя',
  low: 'Низкая',
};

export type FeedFilters = {
  view: FeedView;
  polarity: Polarity | null;
  marker: Marker | null;
  significance: Significance | null;
  days: number;
  all: boolean;
};

export function isMarker(value: string | null): value is Marker {
  return value !== null && Object.hasOwn(MARKER_TITLES, value);
}

export function isSignificance(value: string | null): value is Significance {
  return value !== null && Object.hasOwn(SIGNIFICANCE_TITLES, value);
}

function readDays(value: string | null): number {
  const days = Number(value);
  return PERIODS.some((period) => period === days) ? days : DEFAULT_DAYS;
}

export function readFeedFilters(params: URLSearchParams): FeedFilters {
  const rawPolarity = params.get('polarity');
  const rawMarker = params.get('marker');
  const significance = params.get('significance');
  return {
    view: params.get('view') === 'related' ? 'related' : 'feed',
    polarity: rawPolarity === 'risk' || rawPolarity === 'opportunity' ? rawPolarity : null,
    marker: isMarker(rawMarker) ? rawMarker : null,
    significance: isSignificance(significance)
      ? significance
      : params.get('important') === '1'
        ? 'high'
        : null,
    days: readDays(params.get('days')),
    all: params.get('all') === '1',
  };
}

export function updateFilterParams(params: URLSearchParams, changes: Partial<FeedFilters>) {
  const filters = { ...readFeedFilters(params), ...changes };
  const next = new URLSearchParams(params);
  const values = {
    view: filters.view === 'feed' ? null : filters.view,
    polarity: filters.polarity,
    marker: filters.marker,
    significance: filters.significance,
    days: filters.days === DEFAULT_DAYS ? null : String(filters.days),
    all: filters.all ? '1' : null,
    important: null,
  };
  Object.entries(values).forEach(([key, value]) => {
    if (value === null) next.delete(key);
    else next.set(key, value);
  });
  return next;
}

export function feedSearch(params: URLSearchParams, context = ''): string {
  const next = new URLSearchParams();
  const fallback = new URLSearchParams(context);
  for (const key of ['company', 'role']) {
    const value = fallback.get(key) ?? params.get(key);
    if (value) next.set(key, value);
  }
  return `?${updateFilterParams(next, readFeedFilters(params))}`;
}

export function hasActiveFilters(filters: FeedFilters): boolean {
  return Boolean(
    filters.polarity ||
    filters.marker ||
    filters.significance ||
    filters.days !== DEFAULT_DAYS ||
    filters.all,
  );
}

export function useFeedFilters(context = '') {
  const [params, setParams] = useSearchParams();
  const filters = readFeedFilters(params);
  const update = (changes: Partial<FeedFilters>) =>
    setParams((current) => updateFilterParams(current, changes), { preventScrollReset: true });

  return {
    filters,
    search: feedSearch(params, context),
    setView: (view: FeedView) => update({ view }),
    setPolarity: (polarity: Polarity | null) => update({ polarity }),
    setMarker: (marker: Marker | null) => update({ marker }),
    setSignificance: (significance: Significance | null) => update({ significance }),
    setDays: (days: number) => update({ days: readDays(String(days)) }),
    setAll: (all: boolean) => update({ all }),
    reset: () =>
      update({ polarity: null, marker: null, significance: null, days: DEFAULT_DAYS, all: false }),
  };
}

export function applyFilters(
  items: FeedItem[],
  filters: Pick<FeedFilters, 'polarity' | 'marker' | 'significance'>,
): FeedItem[] {
  return items.filter(
    (item) =>
      (!filters.polarity || item.event.polarity === filters.polarity) &&
      (!filters.marker || (item.event.markers ?? []).includes(filters.marker)) &&
      (!filters.significance || item.significance === filters.significance),
  );
}
