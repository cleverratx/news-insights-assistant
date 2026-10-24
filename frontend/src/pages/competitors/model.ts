import { PERIODS } from '@/features/feed/filters';
import type { GeneratedInsight, LabeledStatement, RelatedFeed } from '@/shared/api/types';
import type { Role } from '@/shared/lib/demo';

export type RelatedEvent = NonNullable<RelatedFeed['items']>[number];
export type CompanyMention = RelatedEvent['companies'][number];
export type Scope = RelatedFeed['scope'];

export const SCOPES: Record<Scope, string> = {
  all: 'Все события',
  competitors: 'Конкуренты',
  counterparties: 'Контрагенты',
};

export function readRelatedFilters(params: URLSearchParams) {
  const rawScope = params.get('scope');
  const scope: Scope = rawScope && Object.hasOwn(SCOPES, rawScope) ? (rawScope as Scope) : 'all';
  const days = Number(params.get('days'));
  return { scope, days: PERIODS.some((period) => period === days) ? days : 7 };
}

export function relatedFeedPath(company: string, scope: Scope, days: number) {
  const params = new URLSearchParams({ scope, days: String(days), limit: '50' });
  return `/companies/${encodeURIComponent(company)}/related-feed?${params}`;
}

export function relatedContextMatches(
  result: GeneratedInsight,
  company: string,
  mention: CompanyMention,
  role: Role | null,
) {
  return (
    result.insight.company_id === company &&
    result.insight.news_ids.length === 1 &&
    result.insight.news_ids[0] === mention.news_id &&
    result.related?.company.id === mention.id &&
    (result.role ?? null) === role &&
    !result.feed_snapshot_id
  );
}

export function relatedStatements(
  result: GeneratedInsight,
  section: 'digest' | 'meaning',
): LabeledStatement[] {
  const rows = new Map(
    [
      ...(result.presentation.facts ?? []),
      ...(result.presentation.ai_conclusions ?? []),
      ...(result.presentation.bank_position ?? []),
      ...(result.presentation.client_context ?? []),
    ].map((row) => [row.statement.id, row]),
  );
  return [...new Set(result.related?.[section] ?? [])].flatMap((id) => {
    const row = rows.get(id);
    return row?.statement.text.trim() ? [row] : [];
  });
}

export function mentionTitle(mention: CompanyMention) {
  if (mention.relationship === 'competitor') return 'Конкурент';
  const titles = { buyer: 'Покупатель', supplier: 'Поставщик', carrier: 'Перевозчик' };
  return mention.role ? titles[mention.role] : 'Контрагент';
}
