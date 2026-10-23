import type { components } from '@/shared/api/schema';

export type Role = components['schemas']['Role'];

const configured = import.meta.env.VITE_DEMO_COMPANIES?.split(',')
  .map((id) => id.trim())
  .filter(Boolean);

export const DEMO_COMPANIES: readonly string[] = configured?.length
  ? configured
  : ['U_185947', 'U_19717', 'U_3263'];

export const ROLE_TITLES: Record<Role, string> = {
  ceo: 'Генеральный директор',
  cfo: 'Финансовый директор',
  logistics: 'Директор по логистике',
};

export type DemoUser = {
  login: string;
  name: string;
  role: Role;
};

export const DEMO_USERS: DemoUser[] = [
  { login: 'ceo', name: 'Мария Соколова', role: 'ceo' },
  { login: 'cfo', name: 'Андрей Волков', role: 'cfo' },
  { login: 'logistics', name: 'Ирина Ковалёва', role: 'logistics' },
];

export function isRole(value: string | null): value is Role {
  return value !== null && value in ROLE_TITLES;
}
