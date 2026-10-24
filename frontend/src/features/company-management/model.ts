import type { components } from '@/shared/api/schema';
type Schemas = components['schemas'];
export type Competitors = Schemas['Competitors'];
export type Competitor = Schemas['Competitor'];
export type LinkedCompanies = Schemas['LinkedCompanies'];
export type LinkedCompany = Schemas['LinkedCompanyCard'];
export type LinkCreate = Schemas['ManualLinkCreate'];

export const LINK_ROLES: Record<NonNullable<LinkedCompany['role']>, string> = {
  buyer: 'Покупатель',
  supplier: 'Поставщик',
  carrier: 'Перевозчик',
};
export const COMPETITOR_SOURCES: Record<Competitor['source'], string> = {
  client: 'Вы добавили',
  bank_data: 'Данные банка',
  open_source: 'Открытые источники',
};
export const LINK_ORIGINS = {
  client: 'Вы добавили',
  payments: 'По платежам',
  group: 'Группа компаний',
  shared_person: 'Общие участники',
};

export function collectionPath(company: string, collection: 'competitors' | 'links', id?: string) {
  return `/companies/${encodeURIComponent(company)}/${collection}${id === undefined ? '' : `/${encodeURIComponent(id)}`}`;
}
export const isManual = (card: LinkedCompany) =>
  card.company_id == null && card.origins.includes('client');
export const validName = (name: string) =>
  name.trim().length > 0 && Array.from(name.trim()).length <= 300;

export function linkBody(
  name: string,
  role: LinkCreate['role'],
  industry: string,
  okved: string,
  note: string,
): LinkCreate {
  return {
    name: name.trim(),
    role,
    ...(industry.trim() ? { industry: industry.trim() } : {}),
    ...(okved.trim() ? { okved_code: okved.trim() } : {}),
    ...(note.trim() ? { note: note.trim() } : {}),
  };
}
