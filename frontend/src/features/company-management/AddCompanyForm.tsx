import { useEffect, useRef, useState, type FormEvent } from 'react';
import { ButtonDesktop } from '@alfalab/core-components-button/desktop';
import { apiPost, errorMessage, isAbort } from '@/shared/api/client';
import {
  collectionPath,
  LINK_ROLES,
  linkBody,
  validName,
  type LinkCreate,
  type LinkedCompany,
  type Competitor,
} from './model';
import styles from './CompanyManagement.module.css';

export function AddCompanyForm({
  company,
  kind,
  onSaved,
  onCancel,
  onBusy,
}: {
  company: string;
  kind: 'competitors' | 'links';
  onSaved: (name: string) => void;
  onCancel: () => void;
  onBusy: (busy: boolean) => void;
}) {
  const [name, setName] = useState('');
  const [role, setRole] = useState<LinkCreate['role']>('supplier');
  const [industry, setIndustry] = useState('');
  const [okved, setOkved] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);
  const valid =
    validName(name) &&
    Array.from(industry.trim()).length <= 300 &&
    Array.from(okved.trim()).length <= 20 &&
    Array.from(note.trim()).length <= 2000;
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (request.current || !valid) return;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    onBusy(true);
    setError(undefined);
    try {
      await apiPost<Competitor | LinkedCompany>(
        collectionPath(company, kind),
        kind === 'competitors'
          ? { name: name.trim() }
          : linkBody(name, role, industry, okved, note),
        controller.signal,
      );
      controller.signal.throwIfAborted();
      onSaved(name.trim());
    } catch (caught) {
      if (!isAbort(caught)) setError(errorMessage(caught));
    } finally {
      if (!controller.signal.aborted) {
        request.current = null;
        setBusy(false);
        onBusy(false);
      }
    }
  };
  return (
    <form
      className={styles.form}
      onSubmit={(event) => void submit(event)}
      aria-label={kind === 'competitors' ? 'Добавление конкурента' : 'Добавление контрагента'}
    >
      <h3>{kind === 'competitors' ? 'Новый конкурент' : 'Новый контрагент'}</h3>
      <label className={styles.field}>
        <span>Название компании</span>
        <input
          autoFocus
          required
          value={name}
          onChange={(e) => setName(e.target.value)}
          disabled={busy}
          maxLength={300}
          placeholder="Например, ООО «Северный терминал»"
        />
      </label>
      {kind === 'links' && (
        <>
          <div className={styles.fields}>
            <label className={styles.field}>
              <span>Кто это для вашей компании</span>
              <select
                value={role}
                onChange={(e) => setRole(e.target.value as LinkCreate['role'])}
                disabled={busy}
              >
                {Object.entries(LINK_ROLES).map(([key, title]) => (
                  <option value={key} key={key}>
                    {title}
                  </option>
                ))}
              </select>
            </label>
            <label className={styles.field}>
              <span>Отрасль · необязательно</span>
              <input
                value={industry}
                onChange={(e) => setIndustry(e.target.value)}
                disabled={busy}
                maxLength={300}
                placeholder="Например, грузоперевозки"
              />
            </label>
          </div>
          <details>
            <summary>Дополнительная информация</summary>
            <div className={styles.extra}>
              <label className={styles.field}>
                <span>ОКВЭД · необязательно</span>
                <input
                  value={okved}
                  onChange={(e) => setOkved(e.target.value)}
                  disabled={busy}
                  maxLength={20}
                  placeholder="Например, 49.20"
                />
              </label>
              <label className={styles.field}>
                <span>Примечание · необязательно</span>
                <textarea
                  rows={3}
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  disabled={busy}
                  maxLength={2000}
                  placeholder="Что важно учитывать в отношениях с этой компанией"
                />
              </label>
            </div>
          </details>
        </>
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
          size={40}
          disabled={!valid || busy}
          loading={busy}
        >
          Добавить
        </ButtonDesktop>
        <ButtonDesktop type="button" view="secondary" size={40} onClick={onCancel} disabled={busy}>
          Отмена
        </ButtonDesktop>
      </div>
    </form>
  );
}
