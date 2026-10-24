import type { components } from '@/shared/api/schema';

type Schemas = components['schemas'];
export type KnowledgeBase = Schemas['KnowledgeBase'];
export type KnowledgeFact = Schemas['KnowledgeFact'];
export type FactCategory = Schemas['FactCategory'];
export type FactSource = Schemas['FactSource'];
export type FactStatus = Schemas['FactStatus'];
export type FactFilter = {
  query: string;
  category: FactCategory | 'all';
  source: FactSource | 'all';
  status: FactStatus | 'all';
};

export const CATEGORIES: Record<FactCategory, string> = {
  markets: 'Рынки сбыта',
  products: 'Продукты и услуги',
  seasonality: 'Сезонность',
  constraints: 'Ограничения и риски',
  suppliers: 'Поставщики',
  customers: 'Покупатели',
  plans: 'Планы компании',
  other: 'О компании',
};
export const SOURCES: Record<FactSource, string> = {
  bank_data: 'Данные банка',
  system: 'Вывод ИИ',
  client: 'Данные клиента',
  legend: 'Демо-данные',
};
export const EMPTY_FILTER: FactFilter = {
  query: '',
  category: 'all',
  source: 'all',
  status: 'all',
};

const normalize = (value: string) => value.toLocaleLowerCase('ru').replaceAll('ё', 'е');

export function filterFacts(facts: KnowledgeFact[], filter: FactFilter) {
  const terms = normalize(filter.query).trim().split(/\s+/).filter(Boolean);
  return facts.filter((fact) => {
    const text = normalize(`${fact.text} ${CATEGORIES[fact.category]} ${SOURCES[fact.source]}`);
    return (
      !fact.deleted &&
      (filter.category === 'all' || fact.category === filter.category) &&
      (filter.source === 'all' || fact.source === filter.source) &&
      (filter.status === 'all' || fact.status === filter.status) &&
      terms.every((term) => text.includes(term))
    );
  });
}

export function valueOptions(fact: KnowledgeFact): Record<string, string> | null {
  if (fact.key === 'markets.trade_direction')
    return {
      import: 'Импорт',
      export: 'Экспорт',
      both: 'Импорт и экспорт',
    };
  if (fact.key?.startsWith('counterparty.') && fact.key.endsWith('.role'))
    return {
      buyer: 'Покупатель',
      supplier: 'Поставщик',
      carrier: 'Перевозчик',
    };
  if (fact.key?.startsWith('competitors.') && typeof fact.value === 'boolean')
    return {
      true: 'Конкурент',
      false: 'Не является конкурентом',
    };
  return null;
}

export function editPayload(
  fact: KnowledgeFact,
  text: string,
  category: FactCategory,
  choice: string,
) {
  const nextText = text.trim();
  const options = valueOptions(fact);
  const value = options
    ? Object.hasOwn(options, choice)
      ? choice === ''
        ? null
        : typeof fact.value === 'boolean'
          ? choice === 'true'
          : choice
      : (fact.value ?? null)
    : nextText === fact.text
      ? (fact.value ?? null)
      : null;
  return { text: nextText, category, value, expected_version: fact.version };
}

export function validText(text: string) {
  const length = Array.from(text.trim()).length;
  return length > 0 && length <= 2000;
}

export function factPath(company: string, id?: string) {
  const base = `/companies/${encodeURIComponent(company)}/knowledge`;
  return id === undefined ? base : `${base}/facts/${encodeURIComponent(id)}`;
}
