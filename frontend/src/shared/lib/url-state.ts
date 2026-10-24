import { useEffect } from 'react';
import { useSearchParams } from 'react-router';

import { DEMO_COMPANIES, isRole, type Role } from '@/shared/lib/demo';

const ALL_ROLES = 'all';

export type DemoContext = {
  company: string;
  role: Role | null;
  position: string | null;
  search: string;
  setCompany: (company: string) => void;
  setRole: (role: Role | null) => void;
  setPosition: (id: string, role: Role) => void;
};

function isDemoCompany(value: string | null): value is string {
  return value !== null && DEMO_COMPANIES.includes(value);
}

export function useDemoContext(
  defaultRole: Role,
  defaults?: { company?: string; position?: string },
): DemoContext {
  const [params, setParams] = useSearchParams();
  const rawCompany = params.get('company');
  const rawRole = params.get('role');
  const company = isDemoCompany(rawCompany) ? rawCompany : (defaults?.company ?? DEMO_COMPANIES[0]);
  const role = rawRole === ALL_ROLES ? null : isRole(rawRole) ? rawRole : defaultRole;
  const rawPosition =
    params.get('position') ?? (!rawCompany && !rawRole ? defaults?.position : null);
  const position =
    role && rawPosition && /^position:[0-9a-f]{32}$/.test(rawPosition) ? rawPosition : null;
  const context = new URLSearchParams({ company, role: role ?? ALL_ROLES });
  if (position) context.set('position', position);
  const search = `?${context}`;
  useEffect(() => {
    if (position && !params.get('position') && !rawCompany && !rawRole)
      setParams(new URLSearchParams(search), { replace: true });
  }, [position, rawCompany, rawRole, params, setParams, search]);

  const update = (key: string, value: string) =>
    setParams(() => {
      const next = new URLSearchParams(window.location.search);
      next.set(key, value);
      if (key === 'company' || key === 'role') {
        next.delete('position');
        next.delete('snapshot');
      }
      return next;
    });

  return {
    company,
    role,
    position,
    search,
    setCompany: (value) => update('company', value),
    setRole: (value) => update('role', value ?? ALL_ROLES),
    setPosition: (id, baseRole) => {
      const next = new URLSearchParams(window.location.search);
      next.set('role', baseRole);
      next.set('position', id);
      next.delete('snapshot');
      setParams(next);
    },
  };
}
