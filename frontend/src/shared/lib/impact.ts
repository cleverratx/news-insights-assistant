import type { FeedItem } from '@/shared/api/types';

type Impact = FeedItem['impact'];

const LEVELS_RANGE = 6;

export function impactShare(impact: Pick<Impact, 'score' | 'scale'>): number {
  const range = impact.scale === 'levels' ? LEVELS_RANGE : 100;
  return Math.min(Math.abs(impact.score), range) / range;
}

export function scaleLabels(impact: Pick<Impact, 'scale'>): [string, string, string] {
  return impact.scale === 'levels' ? ['риск', '0', 'возможность'] : ['−100', '0', '+100'];
}
