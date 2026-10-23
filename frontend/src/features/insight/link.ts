import { feedSearch } from '@/features/feed/filters';

export function newsLink(search: string, newsId: string, snapshotId?: string | null) {
  const params = new URLSearchParams(feedSearch(new URLSearchParams(search)));
  if (snapshotId) params.set('snapshot', snapshotId);
  return { pathname: `/news/${encodeURIComponent(newsId)}`, search: `?${params}` };
}
