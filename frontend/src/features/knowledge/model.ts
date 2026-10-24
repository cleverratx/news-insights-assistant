import type { components } from '@/shared/api/schema';

type Schemas = components['schemas'];
export type Question = Schemas['Question'];
export type KnowledgeGaps = Schemas['KnowledgeGaps'];
export type KnowledgeFact = Schemas['KnowledgeFact'];
export type Answer = Omit<Schemas['Answer'], 'not_applicable'> & { not_applicable?: boolean };
export type QuickAnswer = { title: string; body: Answer };

export function quickAnswers(key: string): QuickAnswer[] {
  switch (key) {
    case 'markets.sales_regions':
      return [
        {
          title: 'Только Россия',
          body: { text: 'Основные рынки сбыта компании находятся в России.' },
        },
      ];
    case 'seasonality.peak':
      return [
        {
          title: 'Сезонности нет',
          body: { text: 'Выраженной сезонности в продажах и закупках нет.', value: false },
        },
      ];
    case 'constraints.main':
      return [
        {
          title: 'Не хватает кадров',
          body: { text: 'Главное ограничение бизнеса сейчас — нехватка кадров.', value: 'labor' },
        },
        {
          title: 'Сложности с логистикой',
          body: {
            text: 'Главное ограничение бизнеса сейчас — сложности с логистикой.',
            value: 'logistics',
          },
        },
        {
          title: 'Нужно финансирование',
          body: {
            text: 'Главное ограничение бизнеса сейчас — доступ к финансированию.',
            value: 'financing',
          },
        },
      ];
    case 'plans.next_year':
      return [
        {
          title: 'Без крупных изменений',
          body: {
            text: 'В ближайший год не планируем крупных инвестиций, выхода на новые рынки или крупных закупок.',
          },
        },
      ];
    default:
      return [];
  }
}

export function textAnswer(text: string): Answer | null {
  const value = text.trim();
  return value && Array.from(value).length <= 2000 ? { text: value } : null;
}

export function unansweredQuestions(
  questions: Question[],
  facts: KnowledgeFact[],
  completed: string[],
) {
  const known = new Set([
    ...facts.filter((fact) => !fact.deleted).map((fact) => fact.key),
    ...completed,
  ]);
  const seen = new Set<string>();
  const priority = { high: 0, medium: 1, low: 2 };
  return questions
    .filter((question) => {
      if (known.has(question.key) || seen.has(question.key)) return false;
      seen.add(question.key);
      return true;
    })
    .sort((a, b) => priority[a.priority] - priority[b.priority]);
}

export function isAnswerFor(fact: KnowledgeFact, company: string, key: string) {
  return (
    fact.company_id === company &&
    fact.key === key &&
    !fact.deleted &&
    fact.source === 'client' &&
    fact.status === 'confirmed'
  );
}

export function questionPath(company: string, key?: string) {
  const path = `/companies/${encodeURIComponent(company)}/knowledge/questions`;
  return key === undefined ? path : `${path}/${encodeURIComponent(key)}/answer`;
}

export function factAnchor(id: string) {
  return `knowledge-fact-${id}`;
}

export const QUESTION_TOPICS: Record<Question['category'], string> = {
  markets: 'Рынки сбыта',
  products: 'Продукты и услуги',
  seasonality: 'Сезонность',
  constraints: 'Ограничения бизнеса',
  suppliers: 'Поставщики',
  customers: 'Покупатели',
  plans: 'Планы компании',
  other: 'О компании',
};

export function questionTitle(question: Question) {
  if (question.key === 'markets.sales_regions') return 'География продаж';
  if (question.key === 'markets.export_share') return 'Экспорт и импорт';
  return QUESTION_TOPICS[question.category];
}
