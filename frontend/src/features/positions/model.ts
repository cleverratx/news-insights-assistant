import type { components } from '@/shared/api/schema';

export type Position = components['schemas']['PositionProfile'];
export type PositionQuestion = components['schemas']['PositionQuestion'];
export const positionPath = (company: string, id?: string) =>
  `/companies/${encodeURIComponent(company)}/positions${id ? `/${encodeURIComponent(id)}` : ''}`;
export const BASE_ROLES = {
  ceo: 'Управление бизнесом',
  cfo: 'Финансы',
  logistics: 'Логистика',
} as const;
export const QUESTION_LABELS: Record<PositionQuestion['key'], string> = {
  responsibilities: 'Зона ответственности',
  decisions: 'Решения',
  metrics: 'Показатели',
  horizon: 'Горизонт планирования',
};
export function validPosition(title: string, context: string) {
  return (
    title.trim().length > 0 &&
    Array.from(title.trim()).length <= 120 &&
    context.trim().length > 0 &&
    Array.from(context.trim()).length <= 2000
  );
}
export function positionSearch(position: Position) {
  return `?${new URLSearchParams({ company: position.company_id, role: position.base_role, position: position.id })}`;
}
