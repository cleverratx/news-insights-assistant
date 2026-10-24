import { useEffect, useRef, useState, type FormEvent } from 'react';
import { ButtonDesktop } from '@alfalab/core-components-button/desktop';
import { CheckmarkMIcon } from '@alfalab/icons-glyph/CheckmarkMIcon';
import { ApiError, apiGet, apiPost, errorMessage, isAbort } from '@/shared/api/client';
import { useApi } from '@/shared/api/useApi';
import { ErrorState, Skeleton } from '@/shared/ui/StateView';
import { positionPath, QUESTION_LABELS, type Position, type PositionQuestion } from './model';
import styles from './Positions.module.css';

export function PositionQuestions({
  position,
  onSaved,
  onBusy,
}: {
  position: Position;
  onSaved: (position: Position) => void;
  onBusy?: (busy: boolean) => void;
}) {
  const questions = useApi<PositionQuestion[]>(
    `${positionPath(position.company_id, position.id)}/questions`,
  );
  const [editing, setEditing] = useState<PositionQuestion['key'] | null>(null);
  const [drafts, setDrafts] = useState<Partial<Record<PositionQuestion['key'], string>>>({});
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [conflict, setConflict] = useState(false);
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);
  const active = questions.data?.find((q) => !position.answers?.[q.key]);
  const key = editing ?? active?.key;
  const question = questions.data?.find((q) => q.key === key);
  const text = key ? (drafts[key] ?? position.answers?.[key]?.text ?? '') : '';
  const valid = text.trim().length > 0 && Array.from(text.trim()).length <= 1000;
  const send = async (value: string) => {
    if (!key || request.current || !value.trim() || Array.from(value.trim()).length > 1000) return;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    onBusy?.(true);
    setError(undefined);
    try {
      const updated = await apiPost<Position>(
        `${positionPath(position.company_id, position.id)}/questions/${key}/answer`,
        { text: value.trim(), expected_version: position.version },
        controller.signal,
      );
      controller.signal.throwIfAborted();
      onSaved(updated);
      setEditing(null);
    } catch (caught) {
      if (!isAbort(caught)) {
        setError(errorMessage(caught));
        setConflict(caught instanceof ApiError && caught.status === 409);
      }
    } finally {
      if (!controller.signal.aborted) {
        setBusy(false);
        onBusy?.(false);
        request.current = null;
      }
    }
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (valid) void send(text);
  };
  const reload = async () => {
    if (request.current) return;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    onBusy?.(true);
    try {
      const latest = await apiGet<Position>(
        positionPath(position.company_id, position.id),
        controller.signal,
      );
      controller.signal.throwIfAborted();
      setEditing(key ?? null);
      onSaved(latest);
      setConflict(false);
      setError(undefined);
    } catch (caught) {
      if (!isAbort(caught)) setError(errorMessage(caught));
    } finally {
      if (!controller.signal.aborted) {
        request.current = null;
        setBusy(false);
        onBusy?.(false);
      }
    }
  };
  return (
    <section className={styles.questions} aria-label="Уточнения должности">
      {conflict && (
        <button
          className={styles.linkButton}
          type="button"
          disabled={busy}
          onClick={() => void reload()}
        >
          Загрузить актуальный контекст
        </button>
      )}
      <div>
        <h2>Помогите уточнить вашу роль</h2>
        <p className={styles.hint}>
          Ответы сохраняются в контексте должности. К ним можно вернуться позже.
        </p>
      </div>
      {questions.loading && <Skeleton lines={2} />}
      {questions.error && (
        <ErrorState
          title="Не удалось загрузить вопросы"
          message={questions.error}
          onRetry={questions.retry}
        />
      )}
      <div className={styles.answers}>
        {Object.entries(position.answers ?? {}).map(([answerKey, answer]) => (
          <div className={styles.answer} key={answerKey}>
            <span className={styles.confirmed}>
              <CheckmarkMIcon aria-hidden="true" />
              {QUESTION_LABELS[answerKey as PositionQuestion['key']]}
            </span>
            <p>{answer.text}</p>
            <button
              className={styles.linkButton}
              type="button"
              disabled={busy}
              onClick={() => {
                setEditing(answerKey as PositionQuestion['key']);
                setError(undefined);
              }}
            >
              Изменить
            </button>
          </div>
        ))}
      </div>
      {key && (
        <form className={styles.form} aria-label="Ответ о должности" onSubmit={submit}>
          <h3>{question?.question ?? QUESTION_LABELS[key]}</h3>
          {question?.why && <p className={styles.hint}>{question.why}</p>}
          {key === 'horizon' && (
            <div className={styles.actions}>
              {['Неделя', 'Месяц', 'Квартал', 'Год'].map((period) => (
                <ButtonDesktop
                  type="button"
                  key={period}
                  view="secondary"
                  size={40}
                  disabled={busy}
                  onClick={() =>
                    void send(`Обычный горизонт планирования — ${period.toLocaleLowerCase('ru')}.`)
                  }
                >
                  {period}
                </ButtonDesktop>
              ))}
            </div>
          )}
          <label className={styles.field}>
            <span>Ваш ответ</span>
            <textarea
              rows={3}
              value={text}
              onChange={(e) => setDrafts((current) => ({ ...current, [key]: e.target.value }))}
              disabled={busy}
              aria-invalid={Array.from(text.trim()).length > 1000}
            />
            <small>{Array.from(text.trim()).length} / 1000</small>
          </label>
          {error && (
            <p className={styles.error} role="alert">
              {error}
            </p>
          )}
          <div className={styles.actions}>
            <ButtonDesktop
              type="submit"
              size={40}
              view="primary"
              disabled={busy || !valid}
              loading={busy}
            >
              Сохранить ответ
            </ButtonDesktop>
            {editing && (
              <button
                type="button"
                className={styles.linkButton}
                disabled={busy}
                onClick={() => setEditing(null)}
              >
                Отмена
              </button>
            )}
          </div>
        </form>
      )}
      {questions.data && !key && (
        <p className={styles.confirmed}>
          <CheckmarkMIcon aria-hidden="true" />
          Контекст должности уточнён
        </p>
      )}
    </section>
  );
}
