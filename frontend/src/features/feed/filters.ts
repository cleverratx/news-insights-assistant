import { useSearchParams } from 'react-router';

import { isImportant } from '@/shared/api/feed';
import type { FeedItem } from '@/shared/api/types';
import { MARKER_TITLES, type Marker } from '@/shared/lib/markers';

export type Polarity = 'risk' | 'opportunity';
export type FeedView = 'feed' | 'related';

export const PERIODS = [1, 3, 7, 14] as const;
export const DEFAULT_DAYS = 7;

export type FeedFilters = {
  view: FeedView;
  polarity: Polarity | null;
  marker: Marker | null;
  important: boolean;
  days: number;
  all: boolean;
};

function isMarker(value: string | null): value is Marker {
  return value !== null && value in MARKER_TITLES;
}

function readDays(value: string | null): number {
  const days = Number(value);
  return PERIODS.some((period) => period === days) ? days : DEFAULT_DAYS;
}

export function useFeedFilters() {
  const [params, setParams] = useSearchParams();
  const rawPolarity = params.get('polarity');
  const rawMarker = params.get('marker');
  const filters: FeedFilters = {
    view: params.get('view') === 'related' ? 'related' : 'feed',
    polarity: rawPolarity === 'risk' || rawPolarity === 'opportunity' ? rawPolarity : null,
    marker: isMarker(rawMarker) ? rawMarker : null,
    important: params.get('important') === '1',
    days: readDays(params.get('days')),
    all: params.get('all') === '1',
  };

  const update = (changes: Record<string, string | null>) =>
    setParams((current) => {
      const next = new URLSearchParams(current);
      Object.entries(changes).forEach(([key, value]) => {
        if (value === null) next.delete(key);
        else next.set(key, value);
      });
      return next;
    });

  return {
    filters,
    setView: (view: FeedView) => update({ view: view === 'feed' ? null : view }),
    setPolarity: (polarity: Polarity | null) => update({ polarity }),
    setMarker: (marker: Marker | null) => update({ marker }),
    setImportant: (important: boolean) => update({ important: important ? '1' : null }),
    setDays: (days: number) => update({ days: days === DEFAULT_DAYS ? null : String(days) }),
    setAll: (all: boolean) => update({ all: all ? '1' : null }),
    reset: () => update({ polarity: null, marker: null, important: null }),
  };
}

export function applyFilters(items: FeedItem[], filters: FeedFilters): FeedItem[] {
  return items.filter(
    (item) =>
      (!filters.polarity || item.event.polarity === filters.polarity) &&
      (!filters.marker || (item.event.markers ?? []).includes(filters.marker)) &&
      (!filters.important || isImportant(item)),
  );
}

export function markerCounts(items: FeedItem[]): [Marker, number][] {
  const counts = new Map<Marker, number>();
  items.forEach((item) =>
    (item.event.markers ?? []).forEach((marker) =>
      counts.set(marker, (counts.get(marker) ?? 0) + 1),
    ),
  );
  return [...counts.entries()].sort((a, b) => b[1] - a[1]);
}
