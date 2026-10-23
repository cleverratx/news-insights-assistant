import { ButtonDesktop } from '@alfalab/core-components-button/desktop';

import styles from './StateView.module.css';

export function Skeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className={styles.skeleton} aria-hidden="true">
      {Array.from({ length: lines }, (_, index) => (
        <span key={index} className={styles.line} />
      ))}
    </div>
  );
}

type ErrorProps = { title: string; message: string; onRetry?: () => void };

export function ErrorState({ title, message, onRetry }: ErrorProps) {
  return (
    <div className={styles.error} role="alert">
      <p className={styles.title}>{title}</p>
      <p className={styles.message}>{message}</p>
      {onRetry && (
        <ButtonDesktop view="secondary" size={40} onClick={onRetry}>
          Повторить
        </ButtonDesktop>
      )}
    </div>
  );
}
