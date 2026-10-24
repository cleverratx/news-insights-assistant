import { useState } from 'react';
import { Link, useSearchParams } from 'react-router';
import { PlusMIcon } from '@alfalab/icons-glyph/PlusMIcon';
import { ROLE_TITLES, type Role } from '@/shared/lib/demo';
import { Typography } from '@alfalab/core-components-typography';
import { ButtonDesktop } from '@alfalab/core-components-button/desktop';
import { useShell } from '@/app/shell-context';
import { signInProfile } from '@/app/session';
import { PositionEditor } from '@/features/positions/PositionEditor';
import { PositionQuestions } from '@/features/positions/PositionQuestions';
import {
  ROLE_DESCRIPTIONS,
  positionPath,
  positionSearch,
  type Position,
} from '@/features/positions/model';
import { useApi } from '@/shared/api/useApi';
import { invalidateCached } from '@/shared/api/cache';
import { ErrorState, Skeleton } from '@/shared/ui/StateView';
import styles from '@/features/positions/Positions.module.css';

export function PositionsPage() {
  const { company } = useShell();
  return (
    <div className={styles.page}>
      <header>
        <Typography.Title tag="h1" view="medium">
          Моя должность
        </Typography.Title>
        <p className={styles.lead}>
          Задачи, контекст и уточнения, которые помогают разбирать новости для вашей работы.
        </p>
      </header>
      <Positions key={company} company={company} />
    </div>
  );
}
function Positions({ company }: { company: string }) {
  const data = useApi<Position[]>(positionPath(company));
  if (data.loading)
    return (
      <div className={styles.panel}>
        <Skeleton lines={4} />
      </div>
    );
  if (data.error)
    return (
      <ErrorState
        title="Не удалось загрузить должности"
        message={data.error}
        onRetry={data.retry}
      />
    );
  return data.data ? <PositionList initial={data.data} company={company} /> : null;
}
function PositionList({ initial, company }: { initial: Position[]; company: string }) {
  const [items, setItems] = useState(initial);
  const [params, setParams] = useSearchParams();
  const [editing, setEditing] = useState<Position | 'new' | null>(
    params.get('new') === '1' ? 'new' : null,
  );
  const { position, role, setPosition, user } = useShell();
  const closeEditor = () => {
    setEditing(null);
    const next = new URLSearchParams(window.location.search);
    next.delete('new');
    setParams(next, { replace: true, preventScrollReset: true });
  };
  const saved = (next: Position) => {
    setItems((current) =>
      current.some((item) => item.id === next.id)
        ? current.map((item) => (item.id === next.id ? next : item))
        : [...current, next],
    );
    invalidateCached(`/companies/${encodeURIComponent(company)}/`);
    if (user.position === next.id)
      signInProfile({ ...user, title: next.title, role: next.base_role });
    if (position === next.id) setPosition(next.id, next.base_role);
  };
  return (
    <>
      <div className={styles.header}>
        <p className={styles.hint}>
          {items.length ? `Свои роли: ${items.length}` : 'Выберите базовую роль или добавьте свою.'}
        </p>
        <ButtonDesktop
          view="primary"
          size={48}
          onClick={() => setEditing('new')}
          leftAddons={<PlusMIcon aria-hidden="true" />}
        >
          Новая роль
        </ButtonDesktop>
      </div>
      <div className={styles.roleChoices} aria-label="Базовые роли">
        {(Object.keys(ROLE_TITLES) as Role[]).map((base) => (
          <Link
            key={base}
            className={styles.roleChoice}
            data-selected={!position && role === base}
            to={{ pathname: '/feed', search: `?${new URLSearchParams({ company, role: base })}` }}
          >
            <span>
              <strong>{ROLE_TITLES[base]}</strong>
              <span>{ROLE_DESCRIPTIONS[base]}</span>
            </span>
          </Link>
        ))}
      </div>
      {editing && (
        <section className={styles.panel}>
          <h2>{editing === 'new' ? 'Новая должность' : editing.title}</h2>
          <PositionEditor
            key={editing === 'new' ? 'new' : editing.id}
            company={company}
            initial={editing === 'new' ? undefined : editing}
            onSaved={(next) => {
              saved(next);
              closeEditor();
            }}
            onCancel={closeEditor}
          />
        </section>
      )}
      <div className={styles.list}>
        {items.map((item) => (
          <article
            className={styles.panel}
            key={item.id}
            data-selected={position === item.id}
            aria-label={item.title}
          >
            <div>
              <h2>{item.title}</h2>
              <p className={styles.hint}>
                Своя роль
                {position === item.id ? ' · Выбрана' : ''}
              </p>
            </div>
            <p>{item.context}</p>
            <div className={styles.actions}>
              <Link to={{ pathname: '/feed', search: positionSearch(item) }}>
                Открыть свою ленту
              </Link>
              <button type="button" className={styles.linkButton} onClick={() => setEditing(item)}>
                Изменить должность
              </button>
            </div>
            <PositionQuestions position={item} onSaved={saved} />
          </article>
        ))}
      </div>
    </>
  );
}
