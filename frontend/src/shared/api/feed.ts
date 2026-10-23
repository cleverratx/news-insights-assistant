import { useCachedApi } from '@/shared/api/cache';
import type { Feed, FeedItem } from '@/shared/api/types';
import type { Role } from '@/shared/lib/demo';

const IMPORTANT_LEVELS = new Set<FeedItem['importance']['level']>(['critical', 'important']);

type FeedOptions = { days?: number; all?: boolean };

export function feedPath(company: string, role: Role | null, options: FeedOptions = {}): string {
  const query = new URLSearchParams();
  if (options.days && options.days !== 7) query.set('days', String(options.days));
  if (role) query.set('role', role);
  if (options.all) query.set('min_importance', '0');
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
