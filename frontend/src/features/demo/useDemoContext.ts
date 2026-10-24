import { useLocation, useNavigate, useSearchParams } from 'react-router';

import { companyLocation, readDemoContext } from '@/features/demo/context';
import type { Role } from '@/shared/lib/demo';

export type DemoContext = ReturnType<typeof readDemoContext> & {
  setCompany: (company: string) => void;
  setRole: (role: Role | null) => void;
};

export function useDemoContext(defaultRole: Role): DemoContext {
  const [params, setParams] = useSearchParams();
  const { pathname } = useLocation();
  const navigate = useNavigate();

  return {
    ...readDemoContext(params, defaultRole),
    setCompany: (company) => {
      const next = companyLocation(pathname, params, company, defaultRole);
      if (next) void navigate(next);
    },
    setRole: (role) =>
      setParams((current) => {
        const next = new URLSearchParams(current);
        next.set('role', role ?? 'all');
        return next;
      }),
  };
}
