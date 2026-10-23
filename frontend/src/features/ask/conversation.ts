import type { GeneratedInsight, InsightRequest } from '@/shared/api/types';
import { isRole } from '@/shared/lib/demo';

export const MAX_QUESTION_LENGTH = 600;

export type Turn = {
  id: number;
  question: string;
  focus: string;
} & (
  | { status: 'pending' | 'cancelled' }
  | { status: 'error'; error: string }
  | { status: 'answered'; answer: GeneratedInsight }
);

function excerpt(text: string, limit: number): string {
  const characters = Array.from(text.replace(/\s+/g, ' ').trim());
  return characters.length <= limit
    ? characters.join('')
    : characters.slice(0, limit - 1).join('') + '…';
}

function answerText(result: GeneratedInsight): string {
  return (
    result.role_view?.summary?.trim() ||
    (result.presentation.ai_conclusions ?? []).map((row) => row.statement.text).join(' ') ||
    result.insight.status_reason ||
    result.insight.headline
  );
}

export function questionFocus(question: string, original: GeneratedInsight, turns: Turn[]): string {
  const prompt = question.trim();
  const previous = [...turns].reverse().find((turn) => turn.status === 'answered');
  const context =
    previous?.status === 'answered'
      ? `Предыдущий вопрос: ${excerpt(previous.question, 110)}. Ответ: ${excerpt(answerText(previous.answer), 180)}`
      : `Исходный вывод: ${excerpt(answerText(original) || original.insight.headline, 260)}`;
  const prefix = `Ответь на уточняющий вопрос по этому событию: ${prompt}`;
  if (Array.from(prefix).length > MAX_QUESTION_LENGTH) return prompt;
  const remaining = MAX_QUESTION_LENGTH - Array.from(prefix).length - 2;
  return remaining > 40 ? `${prefix}\n\n${excerpt(context, remaining)}` : prefix;
}

export function questionRequest(original: GeneratedInsight, focus: string): InsightRequest {
  const role = original.role ?? null;
  return {
    news_ids: [...original.insight.news_ids],
    role: isRole(role) ? role : null,
    feed_snapshot_id: original.feed_snapshot_id ?? null,
    related_company_id: original.related?.company.id ?? null,
    focus,
    refresh: false,
  };
}

export function sameContext(original: GeneratedInsight, answer: GeneratedInsight): boolean {
  const expected = original.insight.news_ids;
  const actual = answer.insight.news_ids;
  return (
    original.insight.company_id === answer.insight.company_id &&
    expected.length === actual.length &&
    expected.every((id) => actual.includes(id)) &&
    (original.role ?? null) === (answer.role ?? null) &&
    (original.feed_snapshot_id ?? null) === (answer.feed_snapshot_id ?? null) &&
    (original.related?.company.id ?? null) === (answer.related?.company.id ?? null)
  );
}
