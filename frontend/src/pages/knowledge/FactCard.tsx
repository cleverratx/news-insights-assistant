import { useEffect, useRef, useState } from 'react';
import { ButtonDesktop } from '@alfalab/core-components-button/desktop';
import { CheckmarkMIcon } from '@alfalab/icons-glyph/CheckmarkMIcon';
import { PencilMIcon } from '@alfalab/icons-glyph/PencilMIcon';

import { ApiError, apiGet, apiPatch, apiPost, errorMessage, isAbort } from '@/shared/api/client';
import type { CompanyProfile } from '@/shared/api/types';
import { useApi } from '@/shared/api/useApi';
import { formatDateTime } from '@/shared/lib/format';
import { ErrorState, Skeleton } from '@/shared/ui/StateView';

import { FactEditor } from './FactEditor';
import {
  CATEGORIES,
  SOURCES,
  editPayload,
  factPath,
  type KnowledgeBase,
  type KnowledgeFact,
} from './model';
import styles from './KnowledgePage.module.css';

export function SourceBadge({ source }: { source: KnowledgeFact['source'] }) {
  return (
    <span className={styles.source} data-source={source}>
      <span aria-hidden="true" />
      {SOURCES[source]}
    </span>
  );
}

export function FactCard({
  fact,
  profile,
  onChange,
  onNotice,
}: {
  fact: KnowledgeFact;
  profile?: CompanyProfile;
  onChange: (fact: KnowledgeFact) => void;
  onNotice: (message: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [conflict, setConflict] = useState(false);
  const [currentText, setCurrentText] = useState<string>();
  const request = useRef<AbortController | null>(null);
  const editButton = useRef<HTMLButtonElement>(null);
  const restoreFocus = useRef(false);
  useEffect(() => () => request.current?.abort(), []);
  useEffect(() => {
    if (!editing && restoreFocus.current) {
      editButton.current?.focus();
      restoreFocus.current = false;
    }
  }, [editing, fact.version]);

  const mutate = async (
    body?: ReturnType<typeof editPayload> | { text: string; category: KnowledgeFact['category'] },
  ) => {
    if (request.current) return;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    setError(undefined);
    setConflict(false);
    try {
      const path = factPath(fact.company_id, fact.id);
      const saved = body
        ? await apiPatch<KnowledgeFact>(path, body, controller.signal)
        : await apiPost<KnowledgeFact>(`${path}/confirm`, {}, controller.signal);
      controller.signal.throwIfAborted();
      onChange(saved);
      restoreFocus.current = true;
      setEditing(false);
      setCurrentText(undefined);
      onNotice(
        body
          ? 'Изменения сохранены. Факт подтверждён вами.'
          : 'Факт подтверждён. Спасибо за уточнение.',
      );
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
  const reloadFact = async () => {
    if (request.current) return;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    try {
      const base = await apiGet<KnowledgeBase>(factPath(fact.company_id), controller.signal);
      controller.signal.throwIfAborted();
      const latest = base.facts?.find((item) => item.id === fact.id);
      if (!latest) {
        setError('Факт больше не действует. Отмените правку и обновите страницу.');
        return;
      }
      onChange(latest);
      setCurrentText(latest.text);
      setConflict(false);
      setError(undefined);
    } catch (caught) {
      if (!isAbort(caught)) setError(errorMessage(caught));
    } finally {
      if (!controller.signal.aborted) {
        setBusy(false);
        request.current = null;
      }
    }
  };
  const cancel = () => {
    restoreFocus.current = true;
    setEditing(false);
    setError(undefined);
    setConflict(false);
    setCurrentText(undefined);
  };

  return (
    <article className={styles.factCard} aria-label={fact.text}>
      <div className={styles.factHead}>
        <span className={styles.category}>{CATEGORIES[fact.category]}</span>
        <SourceBadge source={fact.source} />
      </div>
      {editing ? (
        <FactEditor
          fact={fact}
          busy={busy}
          error={error}
          currentText={currentText}
          onSubmit={(body) => void mutate(body)}
          onCancel={cancel}
        />
      ) : (
        <>
          <p className={styles.factText}>{fact.text}</p>
          <div className={styles.factFoot}>
            {fact.status === 'confirmed' ? (
              <span className={styles.confirmed}>
                <CheckmarkMIcon aria-hidden="true" />
                Подтверждено{fact.source === 'client' ? ' вами' : ''}
              </span>
            ) : (
              <span className={styles.assumption}>
                <span aria-hidden="true" />
                Нужно подтвердить
              </span>
            )}
            <div className={styles.actions}>
              {fact.status === 'assumption' && (
                <ButtonDesktop
                  size={40}
                  view="secondary"
                  onClick={() => void mutate()}
                  loading={busy}
                  disabled={busy}
                  leftAddons={<CheckmarkMIcon aria-hidden="true" />}
                >
                  Подтвердить
                </ButtonDesktop>
              )}
              <button
                ref={editButton}
                className={styles.editButton}
                type="button"
                disabled={busy}
                onClick={() => {
                  setEditing(true);
                  setError(undefined);
                  setCurrentText(undefined);
                }}
              >
                <PencilMIcon aria-hidden="true" />
                Изменить
              </button>
            </div>
          </div>
          {error && (
            <p className={styles.error} role="alert">
              {error}
            </p>
          )}
        </>
      )}
      {conflict && (
        <button
          type="button"
          className={styles.textButton}
          onClick={() => void reloadFact()}
          disabled={busy}
        >
          Загрузить актуальный факт
        </button>
      )}
      <FactDetails key={`${fact.id}:${fact.version}`} fact={fact} profile={profile} />
    </article>
  );
}

function FactDetails({ fact, profile }: { fact: KnowledgeFact; profile?: CompanyProfile }) {
  const [open, setOpen] = useState(false);
  const history = useApi<KnowledgeFact[]>(
    open
      ? `${factPath(fact.company_id)}/history?${new URLSearchParams({ fact_id: fact.id })}`
      : null,
  );
  const versions = [...(history.data ?? [])].sort((a, b) => b.version - a.version);
  const features = new Map(profile?.features?.map((feature) => [feature.id, feature.label]));
  const evidence = (fact.evidence ?? []).map(
    (id) =>
      features.get(id) ??
      (id.startsWith('legend:') ? 'Роль компании из демо-профиля' : 'Данные профиля компании'),
  );
  return (
    <details className={styles.details} onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>Основания и история</summary>
      {open && (
        <div className={styles.detailBody}>
          <div>
            <h3>На чём основан факт</h3>
            {evidence.length ? (
              <ul>
                {[...new Set(evidence)].map((label) => (
                  <li key={label}>{label}</li>
                ))}
              </ul>
            ) : (
              <p>Информация внесена клиентом.</p>
            )}
          </div>
          <div>
            <h3>История изменений</h3>
            {history.loading && <Skeleton lines={2} />}
            {history.error && (
              <ErrorState
                title="История недоступна"
                message={history.error}
                onRetry={history.retry}
              />
            )}
            {history.data && (
              <ol className={styles.history}>
                {versions.map((version) => (
                  <li key={version.version}>
                    <div className={styles.historyMeta}>
                      <SourceBadge source={version.source} />
                      <time dateTime={version.created_at}>
                        {formatDateTime(version.created_at)}
                      </time>
                    </div>
                    <p>{version.text}</p>
                    {version.status === 'confirmed' && (
                      <span className={styles.hint}>
                        Подтверждено{version.source === 'client' ? ' клиентом' : ''}
                      </span>
                    )}
                  </li>
                ))}
                {!versions.length && <li>История изменений пока пуста.</li>}
              </ol>
            )}
          </div>
        </div>
      )}
    </details>
  );
}
