import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Link } from 'react-router';
import { ButtonDesktop } from '@alfalab/core-components-button/desktop';
import { BulbMIcon } from '@alfalab/icons-glyph/BulbMIcon';

import { newsLink } from '@/features/insight/link';
import { apiPost, errorMessage, isAbort } from '@/shared/api/client';
import type { components } from '@/shared/api/schema';
import type { Feed } from '@/shared/api/types';
import { isRole } from '@/shared/lib/demo';
import { formatDateTime, plural } from '@/shared/lib/format';
import { AnalysisProgress } from '@/shared/ui/AnalysisProgress';
import { OriginMark } from '@/shared/ui/OriginMark';
import { ErrorState } from '@/shared/ui/StateView';

import styles from './AnalyticsPage.module.css';

type Digest = components['schemas']['Digest'];
type Props = { company: string; role: string | null; days: number; feed: Feed; search: string };
const SECTIONS = [
  ['events', 'Главные события'],
  ['interplay', 'Как события связаны'],
  ['competitors', 'Конкуренты и контрагенты'],
  ['think_about', 'О чём подумать'],
  ['conclusions', 'Выводы'],
] as const;

export function PeriodDigest({ company, role, days, feed, search }: Props) {
  const [focus, setFocus] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [cancelled, setCancelled] = useState(false);
  const [result, setResult] = useState<Digest>();
  const pending = useRef<AbortController | null>(null);
  useEffect(() => () => pending.current?.abort(), []);

  async function submit(event?: FormEvent) {
    event?.preventDefault();
    if (pending.current) return;
    const controller = new AbortController();
    pending.current = controller;
    setBusy(true);
    setError('');
    setCancelled(false);
    setResult(undefined);
    const requestedRole = isRole(role) ? role : null;
    try {
      const response = await apiPost<Digest>(
        `/companies/${encodeURIComponent(company)}/digest`,
        {
          days,
          role: requestedRole,
          ...(focus.trim() ? { focus: focus.trim() } : {}),
          refresh: false,
        },
        controller.signal,
      );
      if (controller.signal.aborted) return;
      if (
        response.company_id !== company ||
        response.period_days !== days ||
        (response.role ?? null) !== requestedRole
      ) {
        setError('Сводка относится к другому периоду или компании. Повторите запрос.');
      } else setResult(response);
    } catch (failure) {
      if (!controller.signal.aborted && !isAbort(failure)) setError(errorMessage(failure));
    } finally {
      if (pending.current === controller) {
        pending.current = null;
        setBusy(false);
      }
    }
  }

  function cancel() {
    pending.current?.abort();
    pending.current = null;
    setBusy(false);
    setCancelled(true);
  }

  const numbers = new Map(
    (result?.sources ?? []).map((source, index) => [source.news_id, index + 1]),
  );
  const sourceCount = new Set((result?.sources ?? []).map((source) => source.news_id)).size;
  const sourceLink = (id: string) =>
    newsLink(search, id, feed.items?.some((item) => item.news_id === id) ? feed.snapshot_id : null);

  return (
    <section className={`${styles.panel} ${styles.digest}`} aria-labelledby="digest-title">
      <div className={styles.panelHead}>
        <div className={styles.digestHeading}>
          <span className={styles.digestIcon}>
            <BulbMIcon aria-hidden="true" />
          </span>
          <div>
            <h2 id="digest-title">Сводка за период</h2>
            <p className={styles.caption}>Объедините события в общую картину для бизнеса.</p>
          </div>
        </div>
        <span className={styles.digestPeriod}>
          {days} {plural(days, { one: 'день', few: 'дня', many: 'дней' })}
        </span>
      </div>
      <form onSubmit={submit} className={styles.digestForm}>
        <label>
          <span>На что обратить внимание</span>
          <input
            value={focus}
            disabled={busy}
            maxLength={1200}
            onChange={(event) => setFocus(Array.from(event.target.value).slice(0, 600).join(''))}
            placeholder="Например: закупки и стоимость финансирования"
          />
        </label>
        <ButtonDesktop
          type="submit"
          view="primary"
          size={48}
          disabled={busy}
          className={styles.digestSubmit}
        >
          {result ? 'Обновить сводку' : 'Собрать сводку'}
        </ButtonDesktop>
      </form>
      {busy && <AnalysisProgress compact title="Собираем сводку за период" onCancel={cancel} />}
      {cancelled && (
        <p className={styles.caption} role="status">
          Ожидание остановлено. Можно собрать сводку снова.
        </p>
      )}
      {error && (
        <ErrorState
          title="Не удалось собрать сводку"
          message={error}
          onRetry={() => void submit()}
        />
      )}
      {result && (
        <div className={styles.digestResult}>
          <p className={styles.caption} role="status">
            {result.method === 'llm' ? 'Сводка подготовлена ИИ' : 'Сводка по доступным данным'} ·{' '}
            {formatDateTime(result.generated_at)} · по {sourceCount}{' '}
            {plural(sourceCount, { one: 'материалу', few: 'материалам', many: 'материалам' })}
            {result.cache_hit ? ' · сохранённая версия' : ''}
          </p>
          <p className={styles.digestHeadline}>{result.headline}</p>
          <div className={styles.digestSections}>
            {SECTIONS.map(([key, title]) =>
              result[key]?.length ? (
                <section key={key}>
                  <h3>{title}</h3>
                  <ul>
                    {result[key]?.map((point) => (
                      <li key={point.id}>
                        <OriginMark origin={point.origin} />
                        <p>
                          {point.text}
                          <span className={styles.sourceLinks}>
                            {[...new Set(point.news_ids ?? [])].map((id) =>
                              numbers.has(id) ? (
                                <Link
                                  key={id}
                                  to={sourceLink(id)}
                                  aria-label={`Открыть материал ${numbers.get(id)}`}
                                >
                                  {numbers.get(id)}
                                </Link>
                              ) : null,
                            )}
                          </span>
                        </p>
                      </li>
                    ))}
                  </ul>
                </section>
              ) : null,
            )}
          </div>
          {!!result.gaps?.length && (
            <details className={styles.calculation}>
              <summary>Чего не хватило для сводки</summary>
              {result.gaps.map((gap) => (
                <p key={`${gap.kind}:${gap.missing}`}>
                  {gap.missing}. {gap.consequence}
                </p>
              ))}
            </details>
          )}
          <p className={styles.caption}>{result.disclaimer}</p>
        </div>
      )}
    </section>
  );
}
