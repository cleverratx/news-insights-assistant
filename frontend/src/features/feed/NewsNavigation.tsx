import { useEffect, useRef } from 'react';
import { NavLink, useLocation } from 'react-router';

import styles from './NewsNavigation.module.css';

export function NewsNavigation({ search }: { search: string }) {
  const navigation = useRef<HTMLElement>(null);
  const { pathname } = useLocation();
  useEffect(() => {
    const element = navigation.current;
    if (!element) return;
    const revealActive = () => {
      const active = element.querySelector('[aria-current="page"]');
      if (!active) return;
      const bounds = element.getBoundingClientRect();
      const item = active.getBoundingClientRect();
      if (item.right > bounds.right) element.scrollLeft += item.right - bounds.right;
      else if (item.left < bounds.left) element.scrollLeft += item.left - bounds.left;
    };
    revealActive();
    const observer = new ResizeObserver(revealActive);
    observer.observe(element);
    return () => observer.disconnect();
  }, [pathname]);
  const params = new URLSearchParams(search);
  for (const key of ['view', 'snapshot', 'basis']) params.delete(key);

  return (
    <nav ref={navigation} className={styles.tabs} aria-label="Разделы новостей">
      <NavLink to={{ pathname: '/feed', search: `?${params}` }} className={styles.tab}>
        Для вашей компании
      </NavLink>
      <NavLink to={{ pathname: '/competitors', search: `?${params}` }} className={styles.tab}>
        Анализ конкурентов
      </NavLink>
      <NavLink to={{ pathname: '/analytics', search: `?${params}` }} className={styles.tab}>
        Аналитика
      </NavLink>
      <NavLink to={{ pathname: '/knowledge', search: `?${params}` }} className={styles.tab}>
        База знаний
      </NavLink>
    </nav>
  );
}
