import { useCachedApi } from '@/shared/api/cache';
import type { Feed, FeedItem } from '@/shared/api/types';
import type { Role } from '@/shared/lib/demo';
import type { Marker } from '@/shared/lib/markers';

const IMPORTANT_LEVELS = new Set<FeedItem['importance']['level']>(['critical', 'important']);

type FeedOptions = {
  days?: number;
  all?: boolean;
  marker?: Marker | null;
  significance?: FeedItem['significance'] | null;
  limit?: number;
};

export const FEED_LIMIT = 50;

export function feedPath(company: string, role: Role | null, options: FeedOptions = {}): string {
  const query = new URLSearchParams();
  if (options.days && options.days !== 7) query.set('days', String(options.days));
  if (role) query.set('role', role);
  if (options.marker) query.set('markers', options.marker);
  if (options.significance) query.set('significance', options.significance);
  if (options.all || options.significance) {
    query.set('min_importance', '0');
  }
  if (options.limit || options.all) query.set('limit', String(options.limit ?? FEED_LIMIT));
  const search = query.toString();
  return `/companies/${encodeURIComponent(company)}/feed${search ? `?${search}` : ''}`;
}

export function useFeed(company: string, role: Role | null) {
  return useCachedApi<Feed>(feedPath(company, role));
}

export function isImportant(item: FeedItem): boolean {
  return IMPORTANT_LEVELS.has(item.importance.level);
}

export function freshImportant(feed: Feed | undefined, seen: ReadonlySet<string>): FeedItem[] {
  return (feed?.items ?? []).filter((item) => isImportant(item) && !seen.has(item.news_id));
}
