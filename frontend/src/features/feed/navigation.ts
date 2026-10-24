import { newsLink } from '@/features/insight/link';
import type { Feed, RelatedFeed } from '@/shared/api/types';

const KEY = 'alfa-feed-navigation';

type Event = NonNullable<RelatedFeed['items']>[number];
type Context = {
  company: string;
  role: string;
  search: string;
  snapshot: string;
  items: { newsId: string; storyId: string | null }[];
};

function read(): Context[] {
  try {
    const value: unknown = JSON.parse(sessionStorage.getItem(KEY) ?? '[]');
    if (!Array.isArray(value)) return [];
    return value.filter(
      (item): item is Context =>
        item &&
        typeof item === 'object' &&
        typeof item.company === 'string' &&
        typeof item.role === 'string' &&
        typeof item.search === 'string' &&
        typeof item.snapshot === 'string' &&
        Array.isArray(item.items) &&
        item.items.every(
          (entry: unknown) =>
            entry &&
            typeof entry === 'object' &&
            'newsId' in entry &&
            typeof entry.newsId === 'string' &&
            'storyId' in entry &&
            (entry.storyId === null || typeof entry.storyId === 'string'),
        ),
    );
  } catch {
    return [];
  }
}

export function rememberMainFeed(feed: Feed, search: string) {
  if (!feed.snapshot_id) return;
  const role = new URLSearchParams(search).get('role') ?? 'all';
  const context: Context = {
    company: feed.company_id,
    role,
    search,
    snapshot: feed.snapshot_id,
    items: (feed.items ?? []).map((item) => ({
      newsId: item.news_id,
      storyId: item.story_id ?? null,
    })),
  };
  try {
    sessionStorage.setItem(
      KEY,
      JSON.stringify(
        [
          context,
          ...read().filter((item) => item.company !== context.company || item.role !== role),
        ].slice(0, 20),
      ),
    );
  } catch {
    return;
  }
}

export function fullAnalysisLink(search: string, event: Event, fallback?: Feed) {
  const params = new URLSearchParams(search);
  const company = params.get('company');
  const role = params.get('role') ?? 'all';
  const ids = new Set([event.news_id, ...event.publications.map((item) => item.news_id)]);
  const matches = (newsId: string, storyId?: string | null) =>
    ids.has(newsId) || Boolean(event.story_id && storyId === event.story_id);
  const context = read().find((item) => item.company === company && item.role === role);
  const saved = context?.items.find((item) => matches(item.newsId, item.storyId));
  if (saved && context) return newsLink(context.search, saved.newsId, context.snapshot);
  if (fallback?.company_id === company && (fallback.role ?? 'all') === role) {
    const item = fallback.items?.find((item) => matches(item.news_id, item.story_id));
    if (item) return newsLink(search, item.news_id, fallback.snapshot_id);
  }
  return newsLink(search, event.news_id);
}

export function clearFeedNavigation() {
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    return;
  }
}
