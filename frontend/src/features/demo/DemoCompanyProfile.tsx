import { profileHighlights } from '@/features/demo/profile';
import { companyName, type ProfileResult } from '@/shared/api/profiles';
import { formatRubles } from '@/shared/lib/format';
import { ErrorState, Skeleton } from '@/shared/ui/StateView';

import styles from './DemoCompanyProfile.module.css';

type Props = {
  company: string;
  result?: ProfileResult;
  loading: boolean;
  error?: string;
  onRetry: () => void;
};

export function DemoCompanyProfile({ company, result, loading, error, onRetry }: Props) {
  const profile = result?.profile;
  const failure = error ?? result?.error;
  const highlights = profile ? profileHighlights(profile) : [];

  return (
    <section className={styles.profile} aria-label="Профиль демо-компании" aria-busy={loading}>
      <p className={styles.caption}>Лента для компании</p>
      <h2 className={styles.name}>{companyName(result, company)}</h2>
      {loading && <Skeleton lines={2} />}
      {failure && (
        <ErrorState title="Не удалось загрузить профиль" message={failure} onRetry={onRetry} />
      )}
      {profile && (
        <>
          <dl className={styles.facts}>
            <div>
              <dt>Отрасль</dt>
              <dd>{profile.industry.okved_name ?? 'Нет данных'}</dd>
            </div>
            <div>
              <dt>Регион</dt>
              <dd>{profile.location.region ?? 'Нет данных'}</dd>
            </div>
            <div>
              <dt>Выручка за год</dt>
              <dd>
                {profile.scale.revenue_rub != null
                  ? formatRubles(profile.scale.revenue_rub)
                  : 'Нет данных'}
              </dd>
            </div>
          </dl>
          {highlights.length > 0 && (
            <ul className={styles.highlights} aria-label="Особенности профиля">
              {highlights.map((title) => (
                <li key={title}>{title}</li>
              ))}
            </ul>
          )}
        </>
      )}
      <p className={styles.hint}>
        Выберите другую компанию в шапке, чтобы сравнить подборку событий.
      </p>
    </section>
  );
}
