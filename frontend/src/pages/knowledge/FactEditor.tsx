import { useState, type FormEvent } from 'react';
import { ButtonDesktop } from '@alfalab/core-components-button/desktop';

import {
  CATEGORIES,
  editPayload,
  validText,
  valueOptions,
  type FactCategory,
  type KnowledgeFact,
} from './model';
import styles from './KnowledgePage.module.css';

export function FactEditor({
  fact,
  busy,
  error,
  currentText,
  onSubmit,
  onCancel,
}: {
  fact?: KnowledgeFact;
  busy: boolean;
  error?: string;
  currentText?: string;
  onSubmit: (
    body: ReturnType<typeof editPayload> | { text: string; category: FactCategory },
  ) => void;
  onCancel: () => void;
}) {
  const [text, setText] = useState(fact?.text ?? '');
  const [category, setCategory] = useState<FactCategory>(fact?.category ?? 'other');
  const [choice, setChoice] = useState(String(fact?.value ?? ''));
  const options = fact ? valueOptions(fact) : null;
  const length = Array.from(text.trim()).length;
  const unchanged =
    fact &&
    text.trim() === fact.text &&
    category === fact.category &&
    (!options || choice === String(fact.value ?? ''));
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (busy || !validText(text) || unchanged) return;
    onSubmit(fact ? editPayload(fact, text, category, choice) : { text: text.trim(), category });
  };

  return (
    <form
      className={styles.editor}
      onSubmit={submit}
      aria-label={fact ? 'Редактирование факта' : 'Новый факт'}
    >
      <label className={styles.field}>
        <span>Категория</span>
        <select
          value={category}
          onChange={(event) => setCategory(event.target.value as FactCategory)}
          disabled={busy}
        >
          {Object.entries(CATEGORIES).map(([key, title]) => (
            <option key={key} value={key}>
              {title}
            </option>
          ))}
        </select>
      </label>
      <label className={styles.field}>
        <span>Что важно знать о компании</span>
        <textarea
          autoFocus
          rows={4}
          value={text}
          onChange={(event) => setText(event.target.value)}
          disabled={busy}
          required
          aria-invalid={length > 2000}
          placeholder="Например: основной сезон закупок — с сентября по ноябрь"
        />
        <span className={styles.counter} data-invalid={length > 2000}>
          {length} / 2000
        </span>
      </label>
      {options && (
        <label className={styles.field}>
          <span>
            {fact?.key === 'markets.trade_direction'
              ? 'Направление внешней торговли'
              : 'Роль компании'}
          </span>
          <select
            value={choice}
            onChange={(event) => setChoice(event.target.value)}
            disabled={busy}
          >
            {!Object.hasOwn(options, choice) && (
              <option value={choice}>Текущее значение: {choice}</option>
            )}
            {Object.entries(options).map(([key, title]) => (
              <option key={key} value={key}>
                {title}
              </option>
            ))}
          </select>
          <span className={styles.hint}>
            Проверьте, что формулировка выше соответствует выбранному значению.
          </span>
        </label>
      )}
      {currentText && (
        <div className={styles.notice}>
          <strong>Сейчас сохранено</strong>
          <p>{currentText}</p>
          <p>Ваш текст сохранён в форме. Проверьте его перед повторной отправкой.</p>
        </div>
      )}
      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
      <p className={styles.hint}>После сохранения факт будет отмечен как подтверждённый вами.</p>
      <div className={styles.actions}>
        <ButtonDesktop
          type="submit"
          view="primary"
          size={40}
          loading={busy}
          disabled={busy || !validText(text) || Boolean(unchanged)}
        >
          {fact ? 'Сохранить изменения' : 'Добавить факт'}
        </ButtonDesktop>
        <ButtonDesktop type="button" view="secondary" size={40} onClick={onCancel} disabled={busy}>
          Отмена
        </ButtonDesktop>
      </div>
    </form>
  );
}
