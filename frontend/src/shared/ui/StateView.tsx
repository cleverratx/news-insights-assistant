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

type EmptyProps = {
  title: string;
  message?: string;
  action?: { title: string; onClick: () => void };
};

export function EmptyState({ title, message, action }: EmptyProps) {
  return (
    <div className={styles.empty} role="status">
      <p className={styles.title}>{title}</p>
      {message && <p className={styles.message}>{message}</p>}
      {action && (
        <ButtonDesktop view="secondary" size={40} onClick={action.onClick}>
          {action.title}
        </ButtonDesktop>
      )}
    </div>
  );
}

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
