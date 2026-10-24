import type { CompanyProfile } from '@/shared/api/types';

const PRODUCTS = {
  loan: 'Кредиты',
  leasing: 'Лизинг',
  factoring: 'Факторинг',
} as const;

export function profileHighlights(profile: CompanyProfile): string[] {
  const products: string[] = Object.entries(PRODUCTS)
    .filter(([key]) => profile.products?.some((product) => product === key))
    .map(([, title]) => title);
  if (profile.foreign_trade.has_customs_records) {
    const countries = profile.foreign_trade.countries?.join(', ');
    products.push(countries ? `ВЭД: ${countries}` : 'Есть внешнеэкономическая деятельность');
  }
  return products;
}
