import { useOutletContext } from 'react-router';

import type { DemoContext } from '@/features/demo/useDemoContext';
import type { ProfileResult } from '@/shared/api/profiles';
import type { Feed, FeedItem } from '@/shared/api/types';
import type { ApiState } from '@/shared/api/useApi';
import type { DemoUser } from '@/shared/lib/demo';

export type ShellContext = DemoContext & {
  user: DemoUser;
  profiles: ApiState<ProfileResult[]>;
  current?: ProfileResult;
  feed: ApiState<Feed>;
  fresh: FeedItem[];
};

export function useShell(): ShellContext {
  return useOutletContext<ShellContext>();
}
