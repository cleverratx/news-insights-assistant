import { apiGet, errorMessage } from '@/shared/api/client';
import type { CompanyProfile } from '@/shared/api/types';
import { useLoader } from '@/shared/api/useApi';

export type ProfileResult = { id: string; profile?: CompanyProfile; error?: string };

async function loadProfiles(key: string, signal: AbortSignal): Promise<ProfileResult[]> {
  const ids = key.split(',');
  const results = await Promise.allSettled(
    ids.map((id) => apiGet<CompanyProfile>(`/companies/${encodeURIComponent(id)}/profile`, signal)),
  );
  signal.throwIfAborted();
  return results.map((result, index) =>
    result.status === 'fulfilled'
      ? { id: ids[index], profile: result.value }
      : { id: ids[index], error: errorMessage(result.reason) },
  );
}

export function useProfiles(ids: readonly string[]) {
  return useLoader(ids.join(','), loadProfiles);
}

export function companyName(result: ProfileResult | undefined, id: string): string {
  return result?.profile?.legend?.name ?? id;
}
