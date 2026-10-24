import { useEffect, useRef, useState } from 'react';
import { ButtonDesktop } from '@alfalab/core-components-button/desktop';
import { Typography } from '@alfalab/core-components-typography';
import { PlusMIcon } from '@alfalab/icons-glyph/PlusMIcon';
import { CheckmarkMIcon } from '@alfalab/icons-glyph/CheckmarkMIcon';
import { BookMIcon } from '@alfalab/icons-glyph/BookMIcon';

import { useShell } from '@/app/shell-context';
import { NewsNavigation } from '@/features/feed/NewsNavigation';
import { invalidateCached } from '@/shared/api/cache';
import { apiPost, errorMessage, isAbort } from '@/shared/api/client';
import { useApi } from '@/shared/api/useApi';
import { plural } from '@/shared/lib/format';
import { EmptyState, ErrorState, Skeleton } from '@/shared/ui/StateView';

import { FactCard, SourceBadge } from './FactCard';
import { FactEditor } from './FactEditor';
import {
  CATEGORIES,
  SOURCES,
  EMPTY_FILTER,
  factPath,
  filterFacts,
  type FactFilter,
  type KnowledgeBase,
  type KnowledgeFact,
} from './model';
import styles from './KnowledgePage.module.css';

export function KnowledgePage() {
  const { company, search } = useShell();
  return (
    <div className={styles.page}>
      <header>
        <Typography.Title tag="h1" view="medium">
          База знаний
        </Typography.Title>
        <p className={styles.lead}>
          Что мы знаем о вашей компании. Уточняйте факты, чтобы новости и выводы были полезнее.
        </p>
      </header>
      <NewsNavigation search={search} />
      <CompanyKnowledge key={company} company={company} />
    </div>
  );
}

function CompanyKnowledge({ company }: { company: string }) {
  const base = useApi<KnowledgeBase>(factPath(company));
  if (base.loading)
    return (
      <div className={styles.panel} role="status">
        <p>Загружаем факты о компании…</p>
        <Skeleton lines={5} />
      </div>
    );
  if (base.error)
    return (
      <div className={styles.panel}>
        <ErrorState
          title="Не удалось загрузить базу знаний"
          message={base.error}
          onRetry={base.retry}
        />
      </div>
    );
  return base.data ? <KnowledgeContent initial={base.data} /> : null;
}

function KnowledgeContent({ initial }: { initial: KnowledgeBase }) {
  const { current, profiles } = useShell();
  const [facts, setFacts] = useState(initial.facts ?? []);
  const [filter, setFilter] = useState<FactFilter>(EMPTY_FILTER);
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState('');
  const request = useRef<AbortController | null>(null);
  const addButton = useRef<HTMLButtonElement>(null);
  useEffect(() => () => request.current?.abort(), []);

  const saved = (fact: KnowledgeFact) => {
    setFacts((items) =>
      items.some((item) => item.id === fact.id)
        ? items.map((item) => (item.id === fact.id ? fact : item))
        : [...items, fact],
    );
    invalidateCached(`/companies/${encodeURIComponent(initial.company_id)}/`);
    profiles.retry();
  };
  const add = async (body: { text: string; category: KnowledgeFact['category'] }) => {
    if (request.current) return;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    setError(undefined);
    try {
      const fact = await apiPost<KnowledgeFact>(
        `${factPath(initial.company_id)}/facts`,
        body,
        controller.signal,
      );
      controller.signal.throwIfAborted();
      saved(fact);
      setAdding(false);
      setFilter(EMPTY_FILTER);
      setNotice('Новый факт сохранён и подтверждён вами.');
    } catch (caught) {
      if (!isAbort(caught)) setError(errorMessage(caught));
    } finally {
      if (!controller.signal.aborted) {
        setBusy(false);
        request.current = null;
      }
    }
  };
  const total = facts.length;
  const confirmed = facts.filter((fact) => fact.status === 'confirmed').length;
  const pending = total - confirmed;
  const visible = filterFacts(facts, filter);
  const update = <K extends keyof FactFilter>(key: K, value: FactFilter[K]) =>
    setFilter((prev) => ({ ...prev, [key]: value }));
  const filtered =
    filter.query !== '' ||
    filter.category !== 'all' ||
    filter.source !== 'all' ||
    filter.status !== 'all';

  return (
    <>
      <section className={`${styles.panel} ${styles.overview}`} aria-label="Состояние базы знаний">
        <div className={styles.overviewTitle}>
          <span className={styles.bookIcon}>
            <BookMIcon aria-hidden="true" />
          </span>
          <div>
            <h2>Компания в фактах</h2>
            <p className={styles.hint}>Ваши уточнения имеют приоритет при анализе новостей.</p>
          </div>
        </div>
        <div className={styles.progressHead}>
          <strong>
            {confirmed}
            <span className={styles.hint}> из {total} подтверждено</span>
          </strong>
          <button
            type="button"
            className={styles.textButton}
            onClick={() =>
              total ? update('status', pending ? 'assumption' : 'confirmed') : setAdding(true)
            }
          >
            {pending
              ? `${pending} ${plural(pending, { one: 'факт ждёт', few: 'факта ждут', many: 'фактов ждут' })} подтверждения`
              : total
                ? 'Всё подтверждено'
                : 'Добавьте первый факт'}
          </button>
        </div>
        <div
          className={styles.progress}
          role="progressbar"
          aria-label="Подтверждённые факты"
          aria-valuemin={0}
          aria-valuemax={Math.max(1, total)}
          aria-valuenow={confirmed}
        >
          <span style={{ width: `${total ? (confirmed / total) * 100 : 0}%` }} />
        </div>
        <div className={styles.sources} aria-label="Источники фактов">
          {Object.keys(SOURCES).map((source) => {
            const key = source as KnowledgeFact['source'];
            const count = facts.filter((fact) => fact.source === key).length;
            return (
              count > 0 && (
                <button
                  type="button"
                  key={source}
                  aria-pressed={filter.source === key}
                  onClick={() => update('source', filter.source === key ? 'all' : key)}
                >
                  <SourceBadge source={key} />
                  <strong>{count}</strong>
                </button>
              )
            );
          })}
        </div>
      </section>
      <div className={styles.controlsHead}>
        <div className={styles.tabs} role="group" aria-label="Статус фактов">
          {(
            [
              { key: 'all', title: 'Все факты', count: total },
              { key: 'assumption', title: 'Нужно подтвердить', count: pending },
              { key: 'confirmed', title: 'Подтверждено', count: confirmed },
            ] as const
          ).map((item) => (
            <button
              type="button"
              key={item.key}
              aria-pressed={filter.status === item.key}
              onClick={() => update('status', item.key)}
            >
              {item.title}
              <span>{item.count}</span>
            </button>
          ))}
        </div>
        <ButtonDesktop
          ref={addButton}
          size={48}
          view="primary"
          leftAddons={<PlusMIcon aria-hidden="true" />}
          disabled={adding}
          onClick={() => {
            setAdding(true);
            setError(undefined);
            setNotice('');
          }}
        >
          Добавить факт
        </ButtonDesktop>
      </div>
      <div className={styles.feedback} role="status" aria-live="polite">
        {notice && (
          <>
            <CheckmarkMIcon aria-hidden="true" />
            {notice}
          </>
        )}
      </div>
      {adding && (
        <section className={styles.panel} aria-labelledby="add-fact-title">
          <h2 id="add-fact-title">Новый факт</h2>
          <FactEditor
            busy={busy}
            error={error}
            onSubmit={(body) => void add(body)}
            onCancel={() => {
              setAdding(false);
              setError(undefined);
              addButton.current?.focus();
            }}
          />
        </section>
      )}
      <div className={styles.filters}>
        <label className={`${styles.field} ${styles.search}`}>
          <span>Поиск по фактам</span>
          <input
            type="search"
            placeholder="Продукт, рынок, поставщик…"
            value={filter.query}
            onChange={(event) => update('query', event.target.value)}
          />
        </label>
        <label className={styles.field}>
          <span>Категория</span>
          <select
            value={filter.category}
            onChange={(event) => update('category', event.target.value as FactFilter['category'])}
          >
            <option value="all">Все категории</option>
            {Object.entries(CATEGORIES).map(([key, title]) => (
              <option key={key} value={key}>
                {title}
              </option>
            ))}
          </select>
        </label>
        <label className={styles.field}>
          <span>Происхождение</span>
          <select
            value={filter.source}
            onChange={(event) => update('source', event.target.value as FactFilter['source'])}
          >
            <option value="all">Все источники</option>
            {Object.entries(SOURCES).map(([key, title]) => (
              <option key={key} value={key}>
                {title}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className={styles.resultLine}>
        <span>
          Показано {visible.length} из {total}
        </span>
        {filtered && (
          <button
            type="button"
            className={styles.textButton}
            onClick={() => setFilter(EMPTY_FILTER)}
          >
            Сбросить фильтры
          </button>
        )}
      </div>
      {!total ? (
        <div className={styles.panel}>
          <EmptyState
            title="Начнём с того, что знаете вы"
            message="Добавьте информацию о продуктах, рынках или планах компании — она дополнит данные банка."
          />
        </div>
      ) : visible.length === 0 ? (
        <div className={styles.panel}>
          <EmptyState
            title="Подходящих фактов нет"
            message="Измените условия поиска или сбросьте фильтры."
            action={{ title: 'Сбросить фильтры', onClick: () => setFilter(EMPTY_FILTER) }}
          />
        </div>
      ) : (
        <div className={styles.facts}>
          {visible.map((fact) => (
            <FactCard
              key={fact.id}
              fact={fact}
              profile={current?.profile}
              onChange={saved}
              onNotice={setNotice}
            />
          ))}
        </div>
      )}
    </>
  );
}
