import type { RelatedFeed } from '@/shared/api/types';
import { useApi } from '@/shared/api/useApi';
import { formatAgo } from '@/shared/lib/format';
import { ErrorState, Skeleton } from '@/shared/ui/StateView';

import styles from './RelatedList.module.css';

const RELATIONSHIP = { competitor: 'Конкурент', counterparty: 'Контрагент' } as const;

export function RelatedList({ company }: { company: string }) {
  const related = useApi<RelatedFeed>(`/companies/${encodeURIComponent(company)}/related-feed`);
  const items = related.data?.items ?? [];

  return (
    <div className={styles.list}>
      {related.loading && (
        <div className={styles.card}>
          <Skeleton lines={3} />
        </div>
      )}
      {related.error && (
        <div className={styles.card}>
          <ErrorState
            title="Не удалось загрузить события партнёров"
            message={related.error}
            onRetry={related.retry}
          />
        </div>
      )}
      {related.data && items.length === 0 && (
        <p className={styles.card}>
          За период не нашлось новостей о ваших конкурентах и контрагентах.
        </p>
      )}
      {items.map((item) => (
        <article key={item.news_id} className={styles.card}>
          <p className={styles.companies}>
            {item.companies.map((mention) => (
              <span key={mention.id} className={styles.company}>
                {mention.name}
                <span className={styles.relation}> · {RELATIONSHIP[mention.relationship]}</span>
              </span>
            ))}
          </p>
          <h2 className={styles.title}>{item.card.title}</h2>
          {item.summary && <p className={styles.summary}>{item.summary}</p>}
          <p className={styles.meta}>
            {item.card.url ? (
              <a href={item.card.url} target="_blank" rel="noreferrer">
                {item.card.source}
              </a>
            ) : (
              item.card.source
            )}{' '}
            · {formatAgo(item.card.published_at)}
          </p>
        </article>
      ))}
    </div>
  );
}
