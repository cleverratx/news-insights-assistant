import { Link } from 'react-router';
import { Typography } from '@alfalab/core-components-typography';

import styles from './NotFoundPage.module.css';

export function NotFoundPage() {
  return (
    <section className={styles.plate}>
      <Typography.Title tag="h1" view="small">
        Такой страницы нет
      </Typography.Title>
      <p className={styles.text}>Возможно, ссылка устарела. Начните с главной страницы.</p>
      <Link to="/">На главную</Link>
    </section>
  );
}
