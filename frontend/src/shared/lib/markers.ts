import type { components } from '@/shared/api/schema';

export type Marker = components['schemas']['Marker'];

export const MARKER_TITLES: Record<Marker, string> = {
  economy_rate: 'Экономика и ставка',
  fx: 'Валюта',
  regulation_taxes: 'Регулирование и налоги',
  trade_duties: 'ВЭД и пошлины',
  commodities_prices: 'Сырьё и цены',
  logistics: 'Логистика',
  labor: 'Рынок труда',
  technology: 'Технологии',
  industry_event: 'Отраслевое событие',
  incident: 'Происшествие',
};
