import { useEffect, useRef, useState } from 'react';

import { apiPost, ApiError, errorMessage, isAbort } from '@/shared/api/client';
import type { GeneratedInsight } from '@/shared/api/types';

import {
  MAX_QUESTION_LENGTH,
  questionFocus,
  questionRequest,
  sameContext,
  type Turn,
} from './conversation';

export function useAsk(original: GeneratedInsight) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const pending = useRef<{ id: number; controller: AbortController } | null>(null);
  const nextId = useRef(0);
  useEffect(() => () => pending.current?.controller.abort(), []);

  async function run(turn: Pick<Turn, 'id' | 'question' | 'focus'>) {
    if (pending.current) return;
    const controller = new AbortController();
    pending.current = { id: turn.id, controller };
    setTurns((current) =>
      current.some((item) => item.id === turn.id)
        ? current.map((item) => (item.id === turn.id ? { ...turn, status: 'pending' } : item))
        : [...current, { ...turn, status: 'pending' }],
    );
    try {
      const answer = await apiPost<GeneratedInsight>(
        `/companies/${encodeURIComponent(original.insight.company_id)}/insights`,
        questionRequest(original, turn.focus),
        controller.signal,
      );
      if (controller.signal.aborted) return;
      if (!sameContext(original, answer))
        throw new ApiError(
          'Ответ относится к другому контексту. Повторите вопрос в текущем разборе.',
          409,
        );
      setTurns((current) =>
        current.map((item) =>
          item.id === turn.id ? { ...turn, status: 'answered', answer } : item,
        ),
      );
    } catch (error) {
      if (controller.signal.aborted || isAbort(error)) return;
      const message =
        error instanceof ApiError && error.status === 409
          ? 'Контекст ленты изменился. Обновите ленту и заново откройте разбор, чтобы продолжить.'
          : errorMessage(error);
      setTurns((current) =>
        current.map((item) =>
          item.id === turn.id ? { ...turn, status: 'error', error: message } : item,
        ),
      );
    } finally {
      if (pending.current?.controller === controller) pending.current = null;
    }
  }

  function send(question: string): boolean {
    const value = question.trim();
    if (pending.current || !value || Array.from(value).length > MAX_QUESTION_LENGTH) return false;
    void run({
      id: nextId.current++,
      question: value,
      focus: questionFocus(value, original, turns),
    });
    return true;
  }

  function retry(turn: Turn) {
    void run(turn);
  }

  function cancel() {
    const active = pending.current;
    if (!active) return;
    active.controller.abort();
    pending.current = null;
    setTurns((current) =>
      current.map((turn) =>
        turn.id === active.id
          ? { id: turn.id, question: turn.question, focus: turn.focus, status: 'cancelled' }
          : turn,
      ),
    );
  }

  return { turns, busy: turns.some((turn) => turn.status === 'pending'), send, retry, cancel };
}
