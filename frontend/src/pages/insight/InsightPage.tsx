import { useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router';
import { ArrowLeftMIcon } from '@alfalab/icons-glyph/ArrowLeftMIcon';
import { InformationCircleLineMIcon } from '@alfalab/icons-glyph/InformationCircleLineMIcon';

import { useShell } from '@/app/shell-context';
import { InsightLoading } from '@/features/insight/InsightLoading';
import { InsightView } from '@/features/insight/InsightView';
import { StatusNotice } from '@/features/insight/StatusNotice';
import { TraceSteps } from '@/features/insight/TraceSteps';
import { useInsight } from '@/features/insight/useInsight';
import { useFeed } from '@/shared/api/feed';
import type { GeneratedInsight } from '@/shared/api/types';
import { formatAgo, formatDateTime, plural } from '@/shared/lib/format';
import { CriteriaTable } from '@/shared/ui/ImpactCriteria';
import { ImpactMeter } from '@/shared/ui/ImpactMeter';
import { ImportanceRing } from '@/shared/ui/ImportanceRing';
import { ErrorState } from '@/shared/ui/StateView';

import styles from './InsightPage.module.css';

const DISCLAIMER =
  'Разбор носит информационный характер и не является инвестиционной рекомендацией. ' +
  'Решения принимайте с учётом собственной оценки рисков.';

export function InsightPage() {
  const { newsId = '' } = useParams();
  const { company, role, search } = useShell();
  const [params] = useSearchParams();
  const snapshot = params.get('snapshot');
  const insight = useInsight({ company, newsId, role, snapshot });
  const feed = useFeed(company, role);
  const item = feed.data?.items?.find((entry) => entry.news_id === newsId);
  const result = insight.data;
  const ranking = result?.ranking?.find((entry) => entry.news_id === newsId);
  const importance = ranking?.importance ?? item?.importance;
  const impact = ranking?.impact ?? item?.impact;
  const headline =
    result?.role_view?.headline ?? result?.insight.headline ?? item?.card.title ?? 'Разбор события';
  const original = item?.card.title;

  return (
    <div className={styles.page}>
      <Link className={styles.back} to={{ pathname: '/feed', search }}>
        <ArrowLeftMIcon aria-hidden="true" className={styles.backIcon} />
        Новости для бизнеса
      </Link>

      <header className={styles.header}>
        <div className={styles.headBody}>
          {item && (
            <p className={styles.meta}>
              {item.kind === 'bank_analytics' && (
                <span className={styles.bank}>Аналитика Альфа-Банка</span>
              )}
              <span>{item.event.type}</span>
              <span>
                {item.card.source} · {formatAgo(item.card.published_at)}
                {item.coverage > 1 &&
                  ` · пишут ${item.coverage} ${plural(item.coverage, {
                    one: 'издание',
                    few: 'издания',
                    many: 'изданий',
                  })}`}
              </span>
            </p>
          )}
          <h1 className={styles.title}>{headline}</h1>
          {original && original !== headline && <p className={styles.original}>{original}</p>}
          {result?.role_view && (
            <p className={styles.role}>
              <span className={styles.roleTitle}>Для должности «{result.role_view.title}»:</span>{' '}
              {result.role_view.summary ?? result.role_view.focus}
            </p>
          )}
        </div>
        {importance && impact && (
          <div className={styles.scores}>
            <ImportanceRing importance={importance} scale={feed.data?.importance_scale} size="l" />
            <ImpactMeter impact={impact} size="l" />
          </div>
        )}
      </header>

      {impact && <CriteriaTable impact={impact} />}

      <div aria-live="polite" className={styles.body}>
        {insight.loading && <InsightLoading key={insight.key} onCancel={insight.cancel} />}
        {insight.cancelled && (
          <div className={styles.plate}>
            <p>Разбор остановлен.</p>
            <button type="button" className={styles.action} onClick={insight.retry}>
              Запустить снова
            </button>
          </div>
        )}
        {insight.error && (
          <div className={styles.plate}>
            <ErrorState
              title="Не удалось разобрать событие"
              message={insight.error}
              onRetry={insight.retry}
            />
          </div>
        )}
        {result && (
          <>
            <StatusNotice result={result} />
            <InsightView result={result} />
            <aside className={styles.disclaimer} aria-label="Важно знать">
              <InformationCircleLineMIcon className={styles.disclaimerIcon} aria-hidden="true" />
              <p>{result.disclaimer || DISCLAIMER}</p>
            </aside>
            <TraceSteps insightId={result.insight.id} />
            <CacheLine result={result} onRebuild={insight.rebuild} />
          </>
        )}
      </div>
    </div>
  );
}

function CacheLine({ result, onRebuild }: { result: GeneratedInsight; onRebuild: () => void }) {
  const [confirm, setConfirm] = useState(false);

  return (
    <div className={styles.cache}>
      <span>
        {result.cache_hit ? 'Разбор из сохранённых' : 'Разбор собран'}{' '}
        {formatDateTime(result.insight.created_at)}
      </span>
      {confirm ? (
        <span className={styles.confirm}>
          Собрать разбор заново? Агент снова прочитает источники.
          <button type="button" className={styles.action} onClick={onRebuild}>
            Пересобрать
          </button>
          <button type="button" className={styles.link} onClick={() => setConfirm(false)}>
            Отмена
          </button>
        </span>
      ) : (
        <button type="button" className={styles.link} onClick={() => setConfirm(true)}>
          Пересобрать разбор
        </button>
      )}
    </div>
  );
}
