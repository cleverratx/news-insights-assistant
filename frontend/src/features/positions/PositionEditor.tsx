import { useEffect, useRef, useState, type FormEvent } from 'react';
import { ButtonDesktop } from '@alfalab/core-components-button/desktop';
import { ApiError, apiGet, apiPatch, apiPost, errorMessage, isAbort } from '@/shared/api/client';
import { isRole, type Role } from '@/shared/lib/demo';
import { BASE_ROLES, positionPath, validPosition, type Position } from './model';
import styles from './Positions.module.css';

export function PositionEditor({
  company,
  initial,
  onSaved,
  onCancel,
  identity,
}: {
  company: string;
  initial?: Position;
  onSaved: (position: Position) => void;
  onCancel?: () => void;
  identity?: { name: string; onName: (value: string) => void };
}) {
  const [title, setTitle] = useState(initial?.title ?? '');
  const [base, setBase] = useState<Role>(initial?.base_role ?? 'ceo');
  const [context, setContext] = useState(initial?.context ?? '');
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [version, setVersion] = useState(initial?.version ?? 1);
  const [conflict, setConflict] = useState(false);
  const [current, setCurrent] = useState<Position>();
  const [requestId] = useState(() => crypto.randomUUID());
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);
  const valid =
    validPosition(title, context) &&
    (!identity ||
      (identity.name.trim().length > 0 && Array.from(identity.name.trim()).length <= 100));
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (request.current || !valid) return;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    setError(undefined);
    const body = { title: title.trim(), base_role: base, context: context.trim() };
    try {
      const saved = initial
        ? await apiPatch<Position>(
            positionPath(company, initial.id),
            { ...body, expected_version: version },
            controller.signal,
          )
        : await apiPost<Position>(
            positionPath(company),
            { ...body, request_id: requestId },
            controller.signal,
          );
      controller.signal.throwIfAborted();
      onSaved(saved);
    } catch (caught) {
      if (!isAbort(caught)) {
        setError(errorMessage(caught));
        setConflict(caught instanceof ApiError && caught.status === 409);
      }
    } finally {
      if (!controller.signal.aborted) {
        setBusy(false);
        request.current = null;
      }
    }
  };
  const reload = async () => {
    if (!initial || request.current) return;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    try {
      const latest = await apiGet<Position>(positionPath(company, initial.id), controller.signal);
      controller.signal.throwIfAborted();
      setVersion(latest.version);
      setCurrent(latest);
      setConflict(false);
      setError(undefined);
    } catch (caught) {
      if (!isAbort(caught)) setError(errorMessage(caught));
    } finally {
      if (!controller.signal.aborted) {
        request.current = null;
        setBusy(false);
      }
    }
  };
  return (
    <form
      className={styles.form}
      onSubmit={(event) => void submit(event)}
      aria-label={initial ? 'Редактирование должности' : 'Создание должности'}
    >
      {identity && (
        <label className={styles.field}>
          <span>Ваше имя</span>
          <input
            autoComplete="name"
            value={identity.name}
            onChange={(e) => identity.onName(e.target.value)}
            required
            disabled={busy}
            maxLength={100}
          />
        </label>
      )}
      <label className={styles.field}>
        <span>Название должности</span>
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Например, директор по закупкам"
          required
          disabled={busy}
          maxLength={120}
        />
      </label>
      <label className={styles.field}>
        <span>Что ближе к вашим задачам</span>
        <select
          value={base}
          onChange={(e) => {
            if (isRole(e.target.value)) setBase(e.target.value);
          }}
          disabled={busy}
        >
          {Object.entries(BASE_ROLES).map(([key, label]) => (
            <option key={key} value={key}>
              {label}
            </option>
          ))}
        </select>
        <small>Основа подбора новостей. Ваш контекст уточнит акценты и выводы для должности.</small>
      </label>
      <label className={styles.field}>
        <span>Контекст должности</span>
        <textarea
          rows={4}
          value={context}
          onChange={(e) => setContext(e.target.value)}
          placeholder="За что вы отвечаете, какие задачи и события для вас важны"
          disabled={busy}
          required
          aria-invalid={Array.from(context.trim()).length > 2000}
        />
        <small>{Array.from(context.trim()).length} / 2000</small>
      </label>
      {current && (
        <div className={styles.summary}>
          <strong>Сейчас сохранено: {current.title}</strong>
          <p>{current.context}</p>
          <p className={styles.hint}>
            Ваши изменения остались в форме. Проверьте их перед сохранением.
          </p>
        </div>
      )}
      {conflict && initial && (
        <button
          type="button"
          className={styles.linkButton}
          onClick={() => void reload()}
          disabled={busy}
        >
          Загрузить актуальную должность
        </button>
      )}
      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
      <div className={styles.actions}>
        <ButtonDesktop
          type="submit"
          view="primary"
          size={48}
          disabled={!valid || busy}
          loading={busy}
        >
          {initial ? 'Сохранить должность' : 'Продолжить'}
        </ButtonDesktop>
        {onCancel && (
          <ButtonDesktop
            type="button"
            size={48}
            view="secondary"
            onClick={onCancel}
            disabled={busy}
          >
            Отмена
          </ButtonDesktop>
        )}
      </div>
    </form>
  );
}
