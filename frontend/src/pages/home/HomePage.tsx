import { Link } from 'react-router';
import { Typography } from '@alfalab/core-components-typography';

import { useShell } from '@/app/shell-context';
import type { CompanyProfile } from '@/shared/api/types';
import { formatRubles } from '@/shared/lib/format';
import { ErrorState, Skeleton } from '@/shared/ui/StateView';

import styles from './HomePage.module.css';

const PRODUCTS: Record<NonNullable<CompanyProfile['products']>[number], string> = {
  settlement: 'расчётно-кассовое обслуживание',
  guarantee: 'гарантии',
  loan: 'кредиты',
  leasing: 'лизинг',
  deposit: 'депозиты',
  payroll: 'зарплатный проект',
  factoring: 'факторинг',
};

function greeting(hour: number): string {
  if (hour < 5) return 'Доброй ночи';
  if (hour < 12) return 'Доброе утро';
  if (hour < 18) return 'Добрый день';
  return 'Добрый вечер';
}

export function HomePage() {
  const { user, profiles, current, company, search } = useShell();
  const firstName = user.name.split(' ')[0];

  return (
    <div className={styles.page}>
      <Typography.Title tag="h1" view="medium">
        {greeting(new Date().getHours())}, {firstName}
      </Typography.Title>
      <section className={styles.plate} aria-labelledby="company-title">
        {profiles.loading && <Skeleton lines={4} />}
        {profiles.error && (
          <ErrorState
            title="Не удалось загрузить организации"
            message={profiles.error}
            onRetry={profiles.retry}
          />
        )}
        {current?.error && (
          <ErrorState
            title="Не удалось загрузить профиль организации"
            message={current.error}
            onRetry={profiles.retry}
          />
        )}
        {current?.profile && <CompanySummary profile={current.profile} id={company} />}
      </section>
      <section className={styles.plate} aria-labelledby="news-entry-title">
        <Typography.Title tag="h2" view="xsmall" id="news-entry-title">
          Новости для бизнеса
        </Typography.Title>
        <p className={styles.lead}>
          События, которые касаются вашей компании, с разбором рисков и возможностей под вашу
          должность.
        </p>
        <Link className={styles.primaryLink} to={{ pathname: '/feed', search }}>
          Открыть новости
        </Link>
      </section>
    </div>
  );
}

function CompanySummary({ profile, id }: { profile: CompanyProfile; id: string }) {
  const facts = [
    { term: 'Отрасль', value: profile.industry.okved_name },
    { term: 'Регион', value: profile.location.region },
    {
      term: 'Выручка за год',
      value: profile.scale.revenue_rub ? formatRubles(profile.scale.revenue_rub) : null,
    },
    {
      term: 'Продукты банка',
      value: profile.products?.length
        ? profile.products.map((product) => PRODUCTS[product]).join(', ')
        : null,
    },
  ].filter((fact) => fact.value);

  return (
    <>
      <div className={styles.companyHead}>
        <Typography.Title tag="h2" view="small" id="company-title">
          {profile.legend?.name ?? id}
        </Typography.Title>
        <span className={styles.companyId}>{id}</span>
      </div>
      {profile.legend?.description && <p className={styles.lead}>{profile.legend.description}</p>}
      <dl className={styles.facts}>
        {facts.map((fact) => (
          <div key={fact.term} className={styles.fact}>
            <dt>{fact.term}</dt>
            <dd>{fact.value}</dd>
          </div>
        ))}
      </dl>
    </>
  );
}
