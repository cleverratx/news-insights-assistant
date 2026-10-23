import { useEffect, useState } from 'react';

import { Skeleton } from '@/shared/ui/StateView';

import styles from './InsightLoading.module.css';

const STAGES = [
  { from: 0, text: 'Собираем признаки компании…' },
  { from: 6, text: 'Читаем первоисточник…' },
  { from: 14, text: 'Ищем позицию Альфа-Банка…' },
  { from: 24, text: 'Формулируем выводы для вашей компании…' },
  { from: 38, text: 'Проверяем выводы по источникам…' },
];

const BLOCKS = [
  'Что произошло',
  'Почему важно компании',
  'Риски и возможности',
  'Что предпринять',
  'Позиция Альфа-Банка',
  'На чём основано',
];

export function InsightLoading({ onCancel }: { onCancel: () => void }) {
  const [seconds, setSeconds] = useState(0);

  useEffect(() => {
    const started = Date.now();
    const timer = window.setInterval(
      () => setSeconds(Math.floor((Date.now() - started) / 1000)),
      1000,
    );
    return () => window.clearInterval(timer);
  }, []);

  const stage = [...STAGES].reverse().find((item) => seconds >= item.from) ?? STAGES[0];

  return (
    <div className={styles.loading}>
      <div className={styles.status} role="status">
        <span className={styles.pulse} aria-hidden="true" />
        <span className={styles.stage}>{stage.text}</span>
        <span className={styles.timer}>{seconds} с</span>
        <button type="button" className={styles.cancel} onClick={onCancel}>
          Отменить
        </button>
      </div>
      <p className={styles.hint}>
        Разбор занимает до минуты: агент читает источник, ищет позицию банка и проверяет каждый
        вывод.
      </p>
      {BLOCKS.map((title) => (
        <section key={title} className={styles.block} aria-hidden="true">
          <h2 className={styles.title}>{title}</h2>
          <Skeleton lines={2} />
        </section>
      ))}
    </div>
  );
}
