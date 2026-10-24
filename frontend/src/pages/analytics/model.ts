import type { components } from '@/shared/api/schema';
import type { Feed, FeedItem } from '@/shared/api/types';
import { MARKER_TITLES } from '@/shared/lib/markers';

type Topic = components['schemas']['Topic'];
export type Direction = 'risk' | 'opportunity' | 'mixed' | 'neutral';
export type Selection = {
  kind: 'day' | 'marker' | 'topic' | 'direction';
  key: string;
  title: string;
};
export type Day = {
  key: string;
  label: string;
  fullLabel: string;
  risk: number;
  opportunity: number;
  count: number;
};
export type Distribution = { key: string; title: string; count: number };

export const DIRECTION_TITLES: Record<Direction, string> = {
  risk: 'Риски',
  opportunity: 'Возможности',
  mixed: 'Двоякое влияние',
  neutral: 'Без заметного влияния',
};

const TOPICS: Record<Topic, string> = {
  key_rate: 'Ключевая ставка',
  inflation: 'Инфляция',
  fx: 'Валютный рынок',
  commodities: 'Сырьевые рынки',
  trade: 'Внешняя торговля',
  logistics: 'Логистика',
  sanctions: 'Санкции',
  regulation: 'Регулирование',
  taxes: 'Налоги',
  labor: 'Рынок труда',
  consumption: 'Потребительский спрос',
  debt_market: 'Долговой рынок',
  macro: 'Макроэкономика',
  industry: 'Отраслевые изменения',
  regional: 'Регионы',
  company: 'События компаний',
  other: 'Другие темы',
};
const DAY = 86400000;
const isoDate = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Europe/Moscow',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
});
const shortDate = new Intl.DateTimeFormat('ru-RU', {
  timeZone: 'Europe/Moscow',
  day: 'numeric',
});
const fullDate = new Intl.DateTimeFormat('ru-RU', {
  timeZone: 'Europe/Moscow',
  day: 'numeric',
  month: 'long',
});

export function dateKey(value: string | number) {
  const time = typeof value === 'number' ? value : Date.parse(value);
  return Number.isFinite(time) ? isoDate.format(time) : null;
}

export function impactSides(item: FeedItem) {
  const contributions =
    item.impact.scale === 'levels' && item.impact.answers?.length
      ? item.impact.answers.map((answer) => answer.level)
      : item.impact.criteria.map((criterion) => criterion.points);
  const riskPoints = contributions.reduce((sum, points) => sum + Math.max(0, -points), 0);
  const opportunityPoints = contributions.reduce((sum, points) => sum + Math.max(0, points), 0);
  const risk =
    item.impact.scale === 'weighted' && item.impact.risk != null
      ? item.impact.risk
      : contributions.length
        ? riskPoints
        : Math.max(0, -item.impact.score);
  const opportunity =
    item.impact.scale === 'weighted' && item.impact.opportunity != null
      ? item.impact.opportunity
      : contributions.length
        ? opportunityPoints
        : Math.max(0, item.impact.score);
  return { risk, opportunity };
}

export function directionOf(item: FeedItem): Direction {
  const { risk, opportunity } = impactSides(item);
  if (risk > 0 && opportunity > 0) return 'mixed';
  return risk > 0 ? 'risk' : opportunity > 0 ? 'opportunity' : 'neutral';
}

export function uniqueEvents(items: FeedItem[]) {
  const stories = new Map<string, FeedItem>();
  for (const item of items) {
    const key = item.story_id ?? item.news_id;
    if (!stories.has(key)) stories.set(key, item);
  }
  return [...stories.values()];
}

function distribution(items: FeedItem[], kind: 'marker' | 'topic'): Distribution[] {
  const groups = new Map<string, Distribution>();
  for (const item of items) {
    const values = kind === 'marker' ? item.event.markers : item.event.topics;
    for (const key of new Set(values?.length ? values : ['unclassified'])) {
      const title =
        key === 'unclassified'
          ? kind === 'marker'
            ? 'Без маркера'
            : 'Тема не определена'
          : kind === 'marker'
            ? MARKER_TITLES[key as keyof typeof MARKER_TITLES]
            : TOPICS[key as Topic];
      const group = groups.get(key) ?? { key, title: title ?? key, count: 0 };
      group.count += 1;
      groups.set(key, group);
    }
  }
  return [...groups.values()].sort(
    (a, b) => b.count - a.count || a.title.localeCompare(b.title, 'ru'),
  );
}

export function buildAnalytics(feed: Feed) {
  const items = uniqueEvents(feed.items ?? []);
  const end = Date.parse(feed.generated_at);
  const start = end - feed.period_days * DAY;
  const startKey = dateKey(start);
  const endKey = dateKey(end);
  const scales = new Set(items.map((item) => item.impact.scale));
  const units = scales.size > 1 ? 'events' : 'points';
  const byDay = new Map<string, Day>();
  if (startKey && endKey) {
    for (
      let time = Date.parse(`${startKey}T12:00:00+03:00`);
      dateKey(time)! <= endKey;
      time += DAY
    ) {
      const key = dateKey(time)!;
      byDay.set(key, {
        key,
        label: shortDate.format(time),
        fullLabel: fullDate.format(time),
        risk: 0,
        opportunity: 0,
        count: 0,
      });
    }
  }
  const directions: Record<Direction, number> = { risk: 0, opportunity: 0, mixed: 0, neutral: 0 };
  let unplotted = 0;
  for (const item of items) {
    directions[directionOf(item)] += 1;
    const time = Date.parse(item.card.published_at);
    const key = dateKey(time);
    const day = key ? byDay.get(key) : undefined;
    if (!day) {
      unplotted += 1;
      continue;
    }
    const sides = impactSides(item);
    day.risk += units === 'points' ? sides.risk : Number(sides.risk > 0);
    day.opportunity += units === 'points' ? sides.opportunity : Number(sides.opportunity > 0);
    day.count += 1;
  }
  return {
    items,
    days: [...byDay.values()],
    units,
    directions,
    unplotted,
    markers: distribution(items, 'marker'),
    topics: distribution(items, 'topic'),
    important: items.filter((item) => item.significance === 'high').length,
    uncertain: items.filter((item) => item.impact.confidence === 'low').length,
    riskCount: directions.risk + directions.mixed,
    opportunityCount: directions.opportunity + directions.mixed,
  };
}

export type Analytics = ReturnType<typeof buildAnalytics>;

export function selectEvents(items: FeedItem[], selection: Selection | null) {
  if (!selection) return items;
  return items.filter((item) => {
    if (selection.kind === 'day') return dateKey(item.card.published_at) === selection.key;
    if (selection.kind === 'direction') return directionOf(item) === selection.key;
    const values = selection.kind === 'marker' ? item.event.markers : item.event.topics;
    return selection.key === 'unclassified'
      ? !values?.length
      : values?.some((value) => value === selection.key);
  });
}
