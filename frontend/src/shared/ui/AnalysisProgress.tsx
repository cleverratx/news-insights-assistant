import { useEffect, useState } from 'react';
import { ButtonDesktop } from '@alfalab/core-components-button/desktop';
import { DocumentMIcon } from '@alfalab/icons-glyph/DocumentMIcon';
import { ChartLineAscMIcon } from '@alfalab/icons-glyph/ChartLineAscMIcon';
import { ShieldMIcon } from '@alfalab/icons-glyph/ShieldMIcon';

import styles from './AnalysisProgress.module.css';

type Props = {
  compact?: boolean;
  onCancel?: () => void;
  title?: string;
};

const STEPS = [
  { title: 'Событие и источники', Icon: DocumentMIcon },
  { title: 'Влияние на компанию', Icon: ChartLineAscMIcon },
  { title: 'Проверка выводов', Icon: ShieldMIcon },
];

export function AnalysisProgress({ compact = false, onCancel, title }: Props) {
  const [seconds, setSeconds] = useState(0);

  useEffect(() => {
    const started = Date.now();
    const timer = window.setInterval(
      () => setSeconds(Math.floor((Date.now() - started) / 1000)),
      1000,
    );
    return () => window.clearInterval(timer);
  }, []);

  const takingLong = seconds >= 60;
  const elapsed = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;

  return (
    <section className={styles.progress} data-compact={compact} aria-label="Подготовка ответа">
      <div className={styles.heading}>
        <span className={styles.spinner} aria-hidden="true" />
        <div className={styles.status} role="status" aria-atomic="true">
          <p className={styles.title}>
            {title ?? (compact ? 'Готовим ответ на вопрос' : 'Готовим ваш разбор')}
          </p>
          <p className={styles.description}>
            {takingLong
              ? 'Нужно чуть больше времени. Можно продолжить ждать или остановить ожидание.'
              : 'ИИ сопоставляет материалы с профилем компании. Ответ появится здесь.'}
          </p>
        </div>
      </div>
      {!compact && (
        <div className={styles.plan}>
          <p className={styles.caption}>Что входит в разбор</p>
          <ul className={styles.steps}>
            {STEPS.map(({ title, Icon }) => (
              <li key={title}>
                <Icon aria-hidden="true" />
                <span>{title}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className={styles.footer}>
        <span className={styles.elapsed} role="timer" aria-live="off">
          Прошло {elapsed}
        </span>
        {onCancel && (
          <ButtonDesktop size={40} view="secondary" onClick={onCancel}>
            Остановить ожидание
          </ButtonDesktop>
        )}
      </div>
    </section>
  );
}
