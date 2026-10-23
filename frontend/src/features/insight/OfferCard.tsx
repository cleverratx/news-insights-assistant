import { useState } from 'react';

import type { components } from '@/shared/api/schema';

import styles from './OfferCard.module.css';

type Offer = components['schemas']['ProductOffer'];
type OfferAction = components['schemas']['OfferAction'];

const ACTIONS: Record<OfferAction, string> = {
  contact_manager: 'Связаться с менеджером',
  application: 'Оставить заявку',
  callback: 'Заказать звонок',
};

export function OfferCard({ offer }: { offer: Offer }) {
  const [sent, setSent] = useState<OfferAction | null>(null);

  return (
    <div className={styles.offer}>
      <p className={styles.label}>{offer.label}</p>
      <p className={styles.title}>{offer.title}</p>
      <dl className={styles.facts}>
        <div>
          <dt>Почему сейчас</dt>
          <dd>{offer.why}</dd>
        </div>
        <div>
          <dt>Что это даст</dt>
          <dd>{offer.benefit}</dd>
        </div>
      </dl>
      {offer.next_actions.length > 0 && (
        <div className={styles.actions} aria-live="polite">
          {sent ? (
            <p className={styles.sent}>
              Демо: в рабочей версии запрос «{ACTIONS[sent].toLowerCase()}» уйдёт вашему
              персональному менеджеру.
            </p>
          ) : (
            offer.next_actions.map((action, index) => (
              <button
                key={action}
                type="button"
                className={index === 0 ? styles.primary : styles.secondary}
                onClick={() => setSent(action)}
              >
                {ACTIONS[action]}
              </button>
            ))
          )}
        </div>
      )}
      <p className={styles.disclaimer}>{offer.disclaimer}</p>
    </div>
  );
}
