export function newsLink(search: string, newsId: string, snapshotId?: string | null) {
  const current = new URLSearchParams(search);
  const params = new URLSearchParams();
  ['company', 'role'].forEach((key) => {
    const value = current.get(key);
    if (value) params.set(key, value);
  });
  if (snapshotId) params.set('snapshot', snapshotId);
  return { pathname: `/news/${encodeURIComponent(newsId)}`, search: `?${params}` };
}
