import { useId } from 'react';
import { Link } from 'react-router';
import { PlusMIcon } from '@alfalab/icons-glyph/PlusMIcon';
import { useShell } from '@/app/shell-context';
import { useCachedApi } from '@/shared/api/cache';
import { isRole, ROLE_TITLES } from '@/shared/lib/demo';
import { positionPath, type Position } from './model';
import styles from './Positions.module.css';

export function RoleSelect({ label = 'Должность' }: { label?: string }) {
  const { company, role, position, search, user, setRole, setPosition } = useShell();
  const profiles = useCachedApi<Position[]>(positionPath(company));
  const id = useId();
  const createSearch = new URLSearchParams(search);
  createSearch.set('new', '1');
  return (
    <div className={`${styles.field} ${styles.roleSelect}`}>
      <label htmlFor={id}>{label}</label>
      <div className={styles.roleControl}>
        <select
          id={id}
          value={position ?? role ?? ''}
          onChange={(e) => {
            const selected = profiles.data?.find((item) => item.id === e.target.value);
            if (selected) setPosition(selected.id, selected.base_role);
            else setRole(isRole(e.target.value) ? e.target.value : null);
          }}
        >
          <option value="">Вся компания</option>
          {Object.entries(ROLE_TITLES).map(([key, title]) => (
            <option key={key} value={key}>
              {title}
            </option>
          ))}
          {position && !profiles.data?.some((item) => item.id === position) && (
            <option value={position} disabled>
              {user.position === position ? user.title : 'Своя роль'}
            </option>
          )}
          {profiles.data?.map((item) => (
            <option key={item.id} value={item.id}>
              {item.title}
            </option>
          ))}
        </select>
        <Link
          className={styles.plusRole}
          to={{ pathname: '/positions', search: `?${createSearch}` }}
          aria-label="Добавить новую роль"
          title="Добавить новую роль"
        >
          <PlusMIcon aria-hidden="true" />
        </Link>
      </div>
      {profiles.error && (
        <span className={styles.hint} role="alert">
          Не удалось загрузить свои роли.
        </span>
      )}
    </div>
  );
}
