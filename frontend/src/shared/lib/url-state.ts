import { useSearchParams } from 'react-router';

import { DEMO_COMPANIES, isRole, type Role } from '@/shared/lib/demo';

const ALL_ROLES = 'all';

export type DemoContext = {
  company: string;
  role: Role | null;
  search: string;
  setCompany: (company: string) => void;
  setRole: (role: Role | null) => void;
};

function isDemoCompany(value: string | null): value is string {
  return value !== null && DEMO_COMPANIES.includes(value);
}

export function useDemoContext(defaultRole: Role): DemoContext {
  const [params, setParams] = useSearchParams();
  const rawCompany = params.get('company');
  const rawRole = params.get('role');
  const company = isDemoCompany(rawCompany) ? rawCompany : DEMO_COMPANIES[0];
  const role = rawRole === ALL_ROLES ? null : isRole(rawRole) ? rawRole : defaultRole;
  const search = `?${new URLSearchParams({ company, role: role ?? ALL_ROLES })}`;

  const update = (key: string, value: string) =>
    setParams((current) => {
      const next = new URLSearchParams(current);
      next.set(key, value);
      return next;
    });

  return {
    company,
    role,
    search,
    setCompany: (value) => update('company', value),
    setRole: (value) => update('role', value ?? ALL_ROLES),
  };
}
