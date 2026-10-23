import { useCachedApi } from '@/shared/api/cache';
import type { Feed, FeedItem } from '@/shared/api/types';
import type { Role } from '@/shared/lib/demo';

const IMPORTANT_LEVELS = new Set<FeedItem['importance']['level']>(['critical', 'important']);

export function feedPath(company: string, role: Role | null, background = false): string {
  const query = new URLSearchParams();
  if (role) query.set('role', role);
  if (background) query.set('min_importance', '0');
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
