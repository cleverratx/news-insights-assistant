import type { components } from '@/shared/api/schema';

export type SourceRef = components['schemas']['SourceRef'];

export const SOURCE_TYPE: Record<SourceRef['type'], string> = {
  news: 'Новость',
  bank_analytics: 'Аналитика Альфа-Банка',
  profile_feature: 'Данные вашей компании',
};

function sourceKey(source: SourceRef): string {
  return source.url ?? `${source.type}:${source.ref_id}`;
}

export function numberSources(originals: SourceRef[]): Map<string, number> {
  const byKey = new Map<string, number>();
  return new Map(
    originals.map((source) => {
      const key = sourceKey(source);
      if (!byKey.has(key)) byKey.set(key, byKey.size + 1);
      return [source.id, byKey.get(key) ?? 0];
    }),
  );
}

export function sourceAnchor(number: number): string {
  return `source-${number}`;
}
