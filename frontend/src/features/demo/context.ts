import { DEMO_COMPANIES, isRole, type Role } from '@/shared/lib/demo';

const ALL_ROLES = 'all';

export function readDemoContext(params: URLSearchParams, defaultRole: Role) {
  const rawCompany = params.get('company');
  const rawRole = params.get('role');
  const company =
    rawCompany && DEMO_COMPANIES.includes(rawCompany) ? rawCompany : DEMO_COMPANIES[0];
  const role = rawRole === ALL_ROLES ? null : isRole(rawRole) ? rawRole : defaultRole;
  const search = `?${new URLSearchParams({ company, role: role ?? ALL_ROLES })}`;
  return { company, role, search };
}

export function companyLocation(
  pathname: string,
  params: URLSearchParams,
  company: string,
  defaultRole: Role,
) {
  const current = readDemoContext(params, defaultRole);
  if (!DEMO_COMPANIES.includes(company) || company === current.company) return null;
  const next = new URLSearchParams(params);
  next.set('company', company);
  next.set('role', current.role ?? ALL_ROLES);
  next.delete('snapshot');
  next.delete('basis');
  return {
    pathname: pathname.startsWith('/news/') ? '/feed' : pathname,
    search: `?${next}`,
  };
}
