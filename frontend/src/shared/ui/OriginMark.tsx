import type { components } from '@/shared/api/schema';

import styles from './OriginMark.module.css';

export type Origin = components['schemas']['Origin'];

export const ORIGIN_TITLES: Record<Origin, string> = {
  fact: 'Факт',
  bank: 'Позиция Альфа-Банка',
  client_data: 'Данные вашей компании',
  model: 'Вывод ИИ',
};

const ORIGIN_CLASS: Record<Origin, string> = {
  fact: styles.fact,
  bank: styles.bank,
  client_data: styles.client,
  model: styles.model,
};

export function OriginMark({ origin }: { origin: Origin }) {
  return (
    <span className={`${styles.mark} ${ORIGIN_CLASS[origin]}`}>
      <span className={styles.dot} aria-hidden="true" />
      {ORIGIN_TITLES[origin]}
    </span>
  );
}
