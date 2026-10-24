import { NavLink } from 'react-router';

import styles from './NewsNavigation.module.css';

export function NewsNavigation({ search }: { search: string }) {
  const params = new URLSearchParams(search);
  for (const key of ['view', 'snapshot', 'basis']) params.delete(key);

  return (
    <nav className={styles.tabs} aria-label="Разделы новостей">
      <NavLink to={{ pathname: '/feed', search: `?${params}` }} className={styles.tab}>
        Для вашей компании
      </NavLink>
      <NavLink to={{ pathname: '/competitors', search: `?${params}` }} className={styles.tab}>
        Анализ конкурентов
      </NavLink>
    </nav>
  );
}
