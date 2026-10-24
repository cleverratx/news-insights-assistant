import { useEffect, useId, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { ButtonDesktop } from '@alfalab/core-components-button/desktop';

import type { GeneratedInsight } from '@/shared/api/types';
import { AnalysisProgress } from '@/shared/ui/AnalysisProgress';

import { AnswerContent } from './AnswerContent';
import { MAX_QUESTION_LENGTH } from './conversation';
import { useAsk } from './useAsk';

import styles from './AskPanel.module.css';

const suggestions = [
  'Какие риски для компании?',
  'Что стоит сделать в первую очередь?',
  'На каких данных основан вывод?',
];

export function AskPanel({ result }: { result: GeneratedInsight }) {
  const id = useId();
  const [draft, setDraft] = useState('');
  const [validation, setValidation] = useState('');
  const composer = useRef<HTMLTextAreaElement>(null);
  const transcript = useRef<HTMLOListElement>(null);
  const lastTurn = useRef<HTMLLIElement>(null);
  const { turns, busy, send, retry, cancel } = useAsk(result);
  const length = Array.from(draft).length;
  const hasQuestion = Boolean(draft.trim());

  useEffect(() => {
    const list = transcript.current;
    const last = lastTurn.current;
    if (list && last) list.scrollTo({ top: last.offsetTop, behavior: 'auto' });
  }, [turns]);

  function submit(event?: FormEvent) {
    event?.preventDefault();
    if (busy) return;
    if (!hasQuestion || length > MAX_QUESTION_LENGTH) {
      setValidation(
        !hasQuestion ? 'Напишите вопрос.' : `Сократите вопрос до ${MAX_QUESTION_LENGTH} символов.`,
      );
      composer.current?.focus();
      return;
    }
    if (send(draft)) {
      setDraft('');
      setValidation('');
      composer.current?.focus({ preventScroll: true });
    }
  }

  function edit(question: string) {
    setDraft(question);
    setValidation('');
    composer.current?.focus({ preventScroll: true });
  }

  return (
    <section id={`${id}-chat`} className={styles.panel} aria-labelledby={`${id}-title`}>
      <div className={styles.heading}>
        <div>
          <h3 id={`${id}-title`}>Уточнить у агента</h3>
          <p>Компания, событие и должность — из текущего разбора.</p>
        </div>
        <span className={styles.agentMark} aria-hidden="true">
          ИИ
        </span>
      </div>
      {turns.length === 0 && (
        <div className={styles.suggestions} aria-label="Примеры вопросов">
          {suggestions.map((question) => (
            <button type="button" key={question} onClick={() => edit(question)}>
              {question}
            </button>
          ))}
        </div>
      )}
      {turns.length > 0 && (
        <div
          role="log"
          aria-label="История уточнений"
          aria-live="polite"
          aria-relevant="additions text"
        >
          <ol ref={transcript} className={styles.transcript}>
            {turns.map((turn, index) => (
              <li
                className={styles.turn}
                key={turn.id}
                ref={index === turns.length - 1 ? lastTurn : undefined}
              >
                <div className={styles.question}>
                  <span className={styles.speaker}>Вы</span>
                  <p>{turn.question}</p>
                </div>
                <div className={styles.reply}>
                  <span className={styles.speaker}>Агент</span>
                  {turn.status === 'pending' && <AnalysisProgress compact />}
                  {turn.status === 'answered' && <AnswerContent result={turn.answer} />}
                  {(turn.status === 'error' || turn.status === 'cancelled') && (
                    <div className={styles.failure}>
                      <p role={turn.status === 'error' ? 'alert' : 'status'}>
                        {turn.status === 'error'
                          ? turn.error
                          : 'Ожидание остановлено. Ответ не получен.'}
                      </p>
                      <div className={styles.actions}>
                        <ButtonDesktop
                          size={40}
                          view="secondary"
                          disabled={busy}
                          onClick={() => {
                            retry(turn);
                            composer.current?.focus({ preventScroll: true });
                          }}
                        >
                          Повторить вопрос
                        </ButtonDesktop>
                        <button
                          type="button"
                          className={styles.textButton}
                          onClick={() => edit(turn.question)}
                        >
                          Изменить вопрос
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              </li>
            ))}
          </ol>
        </div>
      )}
      <form className={styles.form} onSubmit={submit}>
        <label className={styles.label} htmlFor={`${id}-question`}>
          Ваш вопрос
        </label>
        <textarea
          ref={composer}
          id={`${id}-question`}
          className={styles.input}
          rows={3}
          maxLength={MAX_QUESTION_LENGTH * 2}
          value={draft}
          placeholder="Например: как это повлияет на наши расходы?"
          aria-describedby={`${id}-hint${validation ? ` ${id}-error` : ''}`}
          aria-invalid={Boolean(validation)}
          onChange={(event) => {
            setDraft(Array.from(event.target.value).slice(0, MAX_QUESTION_LENGTH).join(''));
            setValidation('');
          }}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              submit();
            }
          }}
        />
        {validation && (
          <p className={styles.validation} id={`${id}-error`} role="alert">
            {validation}
          </p>
        )}
        <div className={styles.composerFooter}>
          <span id={`${id}-hint`} className={styles.hint}>
            Enter — отправить · Shift + Enter — новая строка
          </span>
          <span
            className={styles.counter}
            aria-label={`${length} из ${MAX_QUESTION_LENGTH} символов`}
          >
            {length}/{MAX_QUESTION_LENGTH}
          </span>
          {busy ? (
            <ButtonDesktop
              type="button"
              size={40}
              view="secondary"
              onClick={() => {
                cancel();
                composer.current?.focus({ preventScroll: true });
              }}
            >
              Остановить ожидание
            </ButtonDesktop>
          ) : (
            <ButtonDesktop
              className={styles.send}
              type="submit"
              size={48}
              view="primary"
              disabled={!hasQuestion}
            >
              Отправить
            </ButtonDesktop>
          )}
        </div>
      </form>
    </section>
  );
}
