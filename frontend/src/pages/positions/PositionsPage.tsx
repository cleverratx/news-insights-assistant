import { useState } from 'react';
import { Link } from 'react-router';
import { Typography } from '@alfalab/core-components-typography';
import { ButtonDesktop } from '@alfalab/core-components-button/desktop';
import { useShell } from '@/app/shell-context';
import { signInProfile } from '@/app/session';
import { PositionEditor } from '@/features/positions/PositionEditor';
import { PositionQuestions } from '@/features/positions/PositionQuestions';
import {
  BASE_ROLES,
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
  const [editing, setEditing] = useState<Position | 'new' | null>(null);
  const { position, setPosition, user } = useShell();
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
          {items.length
            ? `Должностей в компании: ${items.length}`
            : 'Создайте должность, чтобы настроить контекст вашей работы.'}
        </p>
        <ButtonDesktop view="primary" size={48} onClick={() => setEditing('new')}>
          Создать должность
        </ButtonDesktop>
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
              setEditing(null);
            }}
            onCancel={() => setEditing(null)}
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
                Основа: {BASE_ROLES[item.base_role]}
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
