import { useShell } from '@/app/shell-context';
import { useApi } from '@/shared/api/useApi';
import { isRole, ROLE_TITLES } from '@/shared/lib/demo';
import { positionPath, type Position } from './model';
import styles from './Positions.module.css';

export function RoleSelect({ label = 'Должность' }: { label?: string }) {
  const { company, role, position, user, setRole, setPosition } = useShell();
  const id = position ?? (company === user.company ? user.position : null);
  const profile = useApi<Position>(id ? positionPath(company, id) : null);
  return (
    <label className={`${styles.field} ${styles.roleSelect}`}>
      <span>{label}</span>
      <select
        value={position ? 'custom' : (role ?? '')}
        onChange={(e) => {
          if (e.target.value === 'custom' && profile.data)
            setPosition(profile.data.id, profile.data.base_role);
          else setRole(isRole(e.target.value) ? e.target.value : null);
        }}
      >
        <option value="">Вся компания</option>
        {id && (
          <option value="custom" disabled={!profile.data}>
            {profile.data?.title ?? user.title ?? 'Моя должность'}
          </option>
        )}
        {Object.entries(ROLE_TITLES).map(([key, title]) => (
          <option key={key} value={key}>
            {title}
          </option>
        ))}
      </select>
    </label>
  );
}
