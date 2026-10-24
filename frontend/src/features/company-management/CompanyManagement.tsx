import { useEffect, useId, useRef, useState } from 'react';
import { ButtonDesktop } from '@alfalab/core-components-button/desktop';
import { PlusMIcon } from '@alfalab/icons-glyph/PlusMIcon';
import { ChevronDownMIcon } from '@alfalab/icons-glyph/ChevronDownMIcon';
import { UsersMIcon } from '@alfalab/icons-glyph/UsersMIcon';
import { CheckmarkMIcon } from '@alfalab/icons-glyph/CheckmarkMIcon';
import { apiDelete, apiPost, errorMessage, isAbort } from '@/shared/api/client';
import { invalidateCached } from '@/shared/api/cache';
import { useApi } from '@/shared/api/useApi';
import { formatRubles } from '@/shared/lib/format';
import { ConfirmAction } from '@/shared/ui/ConfirmAction';
import { EmptyState, ErrorState, Skeleton } from '@/shared/ui/StateView';
import { AddCompanyForm } from './AddCompanyForm';
import {
  collectionPath,
  COMPETITOR_SOURCES,
  LINK_ORIGINS,
  LINK_ROLES,
  isManual,
  type Competitors,
  type Competitor,
  type LinkedCompanies,
} from './model';
import styles from './CompanyManagement.module.css';

export function CompanyManagement(props: { company: string; onChanged: () => void }) {
  return <CompanyLists key={props.company} {...props} />;
}

function CompanyLists({ company, onChanged }: { company: string; onChanged: () => void }) {
  const competitors = useApi<Competitors>(collectionPath(company, 'competitors'));
  const links = useApi<LinkedCompanies>(collectionPath(company, 'links'));
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<'competitors' | 'links'>('competitors');
  const [adding, setAdding] = useState<'competitors' | 'links' | null>(null);
  const [query, setQuery] = useState('');
  const [limit, setLimit] = useState(6);
  const [notice, setNotice] = useState('');
  const [formBusy, setFormBusy] = useState(false);
  const id = useId();
  const addCompetitor = useRef<HTMLButtonElement>(null);
  const addCounterparty = useRef<HTMLButtonElement>(null);
  const found = competitors.data ?? competitors.previous;
  const connected = links.data ?? links.previous;
  const excluded = found?.excluded ?? [];
  const activeCompetitors = found?.competitors ?? [];
  const cards = connected?.cards ?? [];
  const match = (name: string) =>
    name
      .toLocaleLowerCase('ru')
      .replaceAll('ё', 'е')
      .includes(query.trim().toLocaleLowerCase('ru').replaceAll('ё', 'е'));
  const visibleCompetitors = activeCompetitors.filter((item) =>
    match(item.name ?? item.company_id ?? item.id),
  );
  const visibleCards = cards.filter((item) => match(item.name ?? item.company_id ?? item.id));
  const changed = () => {
    invalidateCached(`/companies/${encodeURIComponent(company)}/`);
    competitors.retry();
    links.retry();
    onChanged();
  };
  const create = (kind: 'competitors' | 'links') => {
    setOpen(true);
    setTab(kind);
    setAdding(kind);
    setNotice('');
    setQuery('');
    setLimit(6);
  };
  const closeForm = () => {
    setAdding(null);
    setFormBusy(false);
    (tab === 'competitors' ? addCompetitor : addCounterparty).current?.focus();
  };
  const removedCompetitor = async (item: Competitor, signal: AbortSignal) => {
    await apiDelete(collectionPath(company, 'competitors', item.id), signal);
    signal.throwIfAborted();
    changed();
    setNotice(
      item.source === 'client'
        ? 'Конкурент удалён.'
        : 'Конкурент исключён. Его можно вернуть из списка исключённых.',
    );
  };
  return (
    <section className={styles.panel} aria-label="Управление компаниями">
      <div className={styles.header}>
        <div className={styles.intro}>
          <span className={styles.icon}>
            <UsersMIcon aria-hidden="true" />
          </span>
          <div>
            <h2>Компании в подборке</h2>
            <p className={styles.hint}>
              Конкуренты: {found ? activeCompetitors.length : '—'} · Контрагенты и связи:{' '}
              {connected ? cards.length : '—'}
            </p>
          </div>
        </div>
        <button
          className={styles.expand}
          type="button"
          aria-expanded={open}
          aria-controls={id}
          disabled={formBusy}
          onClick={() => setOpen(!open)}
        >
          {open ? 'Свернуть список' : 'Управлять компаниями'}
          <ChevronDownMIcon aria-hidden="true" className={open ? styles.rotated : undefined} />
        </button>
      </div>
      <div className={styles.actions}>
        <ButtonDesktop
          ref={addCompetitor}
          disabled={formBusy}
          view="secondary"
          size={40}
          leftAddons={<PlusMIcon aria-hidden="true" />}
          onClick={() => create('competitors')}
        >
          Добавить конкурента
        </ButtonDesktop>
        <ButtonDesktop
          ref={addCounterparty}
          disabled={formBusy}
          view="secondary"
          size={40}
          leftAddons={<PlusMIcon aria-hidden="true" />}
          onClick={() => create('links')}
        >
          Добавить контрагента
        </ButtonDesktop>
      </div>
      <div className={styles.notice} role="status" aria-live="polite">
        {notice && (
          <>
            <CheckmarkMIcon aria-hidden="true" />
            {notice}
          </>
        )}
      </div>
      {open && (
        <div className={styles.body} id={id}>
          <div className={styles.tabs} role="group" aria-label="Списки компаний">
            {(['competitors', 'links'] as const).map((kind) => (
              <button
                key={kind}
                type="button"
                aria-pressed={tab === kind}
                disabled={formBusy}
                onClick={() => {
                  setTab(kind);
                  setAdding(null);
                  setQuery('');
                  setLimit(6);
                }}
              >
                {kind === 'competitors' ? 'Конкуренты' : 'Контрагенты и связи'}
              </button>
            ))}
          </div>
          {adding && (
            <AddCompanyForm
              key={adding}
              company={company}
              kind={adding}
              onCancel={closeForm}
              onBusy={setFormBusy}
              onSaved={(name) => {
                changed();
                setNotice(`«${name}» — компания добавлена.`);
                closeForm();
              }}
            />
          )}
          <label className={styles.field}>
            <span>Поиск компании в списке</span>
            <input
              type="search"
              placeholder="Название компании"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setLimit(6);
              }}
            />
          </label>
          {tab === 'competitors' ? (
            <>
              {competitors.loading && !found && (
                <div role="status">
                  <p className={styles.hint}>Загружаем конкурентов…</p>
                  <Skeleton lines={3} />
                </div>
              )}
              {competitors.error && (
                <ErrorState
                  title="Не удалось обновить конкурентов"
                  message={competitors.error}
                  onRetry={competitors.retry}
                />
              )}
              {competitors.loading && found && (
                <p className={styles.hint} role="status">
                  Обновляем список…
                </p>
              )}
              {found && (
                <>
                  <div className={styles.cards}>
                    {visibleCompetitors.slice(0, limit).map((item) => (
                      <article
                        className={styles.card}
                        key={item.id}
                        aria-label={item.name ?? item.company_id ?? item.id}
                      >
                        <span className={styles.source} data-source={item.source}>
                          {COMPETITOR_SOURCES[item.source]}
                        </span>
                        <h3>{item.name ?? item.company_id ?? item.id}</h3>
                        {(item.industry || item.region || item.revenue_rub != null) && (
                          <p className={styles.hint}>
                            {[
                              item.industry,
                              item.region,
                              item.revenue_rub != null
                                ? `Выручка ${formatRubles(item.revenue_rub)}`
                                : null,
                            ]
                              .filter(Boolean)
                              .join(' · ')}
                          </p>
                        )}
                        <details className={styles.reasons}>
                          <summary>Почему в списке</summary>
                          {item.reasons.map((reason) => (
                            <p key={reason}>{reason}</p>
                          ))}
                        </details>
                        <ConfirmAction
                          disabled={competitors.loading}
                          label={item.source === 'client' ? 'Удалить' : 'Исключить'}
                          confirmLabel={item.source === 'client' ? 'Удалить' : 'Исключить'}
                          title={
                            item.source === 'client'
                              ? `Удалить «${item.name ?? item.id}»?`
                              : `Исключить «${item.name ?? item.company_id ?? item.id}»?`
                          }
                          description={
                            item.source === 'client'
                              ? 'Уберём компанию из ваших конкурентов и поиска её событий. При необходимости её можно добавить заново.'
                              : 'Компания перестанет участвовать в подборке. Вернуть её можно из списка исключённых.'
                          }
                          onConfirm={(signal) => removedCompetitor(item, signal)}
                        />
                      </article>
                    ))}
                  </div>
                  {!visibleCompetitors.length && (
                    <EmptyState
                      title={query ? 'Компания не найдена' : 'В списке пока нет конкурентов'}
                      message={
                        query
                          ? 'Измените запрос или добавьте компанию.'
                          : 'Назовите конкурента — будем искать события о нём.'
                      }
                    />
                  )}
                  {visibleCompetitors.length > limit && (
                    <button
                      type="button"
                      className={styles.textButton}
                      onClick={() => setLimit(limit + 6)}
                    >
                      Показать ещё {Math.min(6, visibleCompetitors.length - limit)}
                    </button>
                  )}
                  {excluded.length > 0 && (
                    <details className={styles.excluded}>
                      <summary>Исключённые конкуренты · {excluded.length}</summary>
                      <div className={styles.excludedList}>
                        {excluded.map((item) => (
                          <div className={styles.excludedRow} key={item.id}>
                            <span>{item.name ?? item.company_id ?? item.id}</span>
                            <RestoreCompetitor
                              company={company}
                              item={item}
                              disabled={competitors.loading}
                              onRestored={() => {
                                changed();
                                setNotice('Конкурент возвращён в подборку.');
                              }}
                            />
                          </div>
                        ))}
                      </div>
                    </details>
                  )}
                  {found.warnings?.map((warning) => (
                    <p className={styles.hint} key={warning}>
                      {warning}
                    </p>
                  ))}
                </>
              )}
            </>
          ) : (
            <>
              {links.loading && !connected && (
                <div role="status">
                  <p className={styles.hint}>Загружаем контрагентов…</p>
                  <Skeleton lines={3} />
                </div>
              )}
              {links.error && (
                <ErrorState
                  title="Не удалось обновить контрагентов"
                  message={links.error}
                  onRetry={links.retry}
                />
              )}
              {links.loading && connected && (
                <p className={styles.hint} role="status">
                  Обновляем список…
                </p>
              )}
              {connected && (
                <>
                  <div className={styles.cards}>
                    {visibleCards.slice(0, limit).map((item) => (
                      <article
                        className={styles.card}
                        key={item.id}
                        aria-label={item.name ?? item.company_id ?? item.id}
                      >
                        <span
                          className={styles.source}
                          data-source={isManual(item) ? 'client' : 'bank_data'}
                        >
                          {item.origins.map((origin) => LINK_ORIGINS[origin]).join(' · ')}
                        </span>
                        <h3>{item.name ?? item.company_id ?? item.id}</h3>
                        <span className={styles.role}>
                          {item.role ? LINK_ROLES[item.role] : 'Связанная компания'}
                        </span>
                        {(item.industry || item.region) && (
                          <p className={styles.hint}>
                            {[item.industry, item.region].filter(Boolean).join(' · ')}
                          </p>
                        )}
                        {item.note && <p>{item.note}</p>}
                        {item.incoming && (
                          <p className={styles.hint}>
                            Входящие платежи: {formatRubles(item.incoming.amount_rub)} ·{' '}
                            {Math.round(item.incoming.amount_share * 100)}%
                          </p>
                        )}
                        {item.outgoing && (
                          <p className={styles.hint}>
                            Исходящие платежи: {formatRubles(item.outgoing.amount_rub)} ·{' '}
                            {Math.round(item.outgoing.amount_share * 100)}%
                          </p>
                        )}
                        {isManual(item) ? (
                          <ConfirmAction
                            disabled={links.loading}
                            title={`Удалить «${item.name ?? item.id}»?`}
                            description="Удалим добавленную вами связь. Компания перестанет участвовать в подборке событий контрагентов."
                            onConfirm={async (signal) => {
                              await apiDelete(collectionPath(company, 'links', item.id), signal);
                              signal.throwIfAborted();
                              changed();
                              setNotice('Добавленный контрагент удалён.');
                            }}
                          />
                        ) : (
                          <p className={styles.readOnly}>Связь из данных банка</p>
                        )}
                      </article>
                    ))}
                  </div>
                  {!visibleCards.length && (
                    <EmptyState
                      title={query ? 'Компания не найдена' : 'В списке пока нет контрагентов'}
                      message="Добавьте покупателя, поставщика или перевозчика."
                    />
                  )}
                  {visibleCards.length > limit && (
                    <button
                      type="button"
                      className={styles.textButton}
                      onClick={() => setLimit(limit + 6)}
                    >
                      Показать ещё {Math.min(6, visibleCards.length - limit)}
                    </button>
                  )}
                  {!!connected.hidden && (
                    <p className={styles.hint}>
                      Ещё контрагентов по платежам вне этого списка: {connected.hidden}.
                    </p>
                  )}
                  {connected.warnings?.map((warning) => (
                    <p className={styles.hint} key={warning}>
                      {warning}
                    </p>
                  ))}
                </>
              )}
            </>
          )}
        </div>
      )}
    </section>
  );
}

function RestoreCompetitor({
  company,
  item,
  disabled,
  onRestored,
}: {
  company: string;
  item: Competitor;
  disabled: boolean;
  onRestored: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);
  const restore = async () => {
    if (request.current || disabled) return;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    setError(undefined);
    try {
      await apiPost<Competitor>(
        `${collectionPath(company, 'competitors', item.id)}/restore`,
        {},
        controller.signal,
      );
      controller.signal.throwIfAborted();
      onRestored();
    } catch (caught) {
      if (!isAbort(caught)) setError(errorMessage(caught));
    } finally {
      if (!controller.signal.aborted) {
        request.current = null;
        setBusy(false);
      }
    }
  };
  return (
    <div className={styles.restore}>
      <ButtonDesktop
        size={40}
        view="secondary"
        loading={busy}
        disabled={disabled || busy}
        onClick={() => void restore()}
      >
        Вернуть
      </ButtonDesktop>
      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
