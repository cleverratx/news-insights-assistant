import { SelectDesktop } from '@alfalab/core-components-select/desktop';

import { companyName, type ProfileResult } from '@/shared/api/profiles';
import { DEMO_COMPANIES } from '@/shared/lib/demo';

import styles from './DemoCompanySwitcher.module.css';

type Props = {
  company: string;
  results?: ProfileResult[];
  loading: boolean;
  onChange: (company: string) => void;
};

export function DemoCompanySwitcher({ company, results, loading, onChange }: Props) {
  const options = DEMO_COMPANIES.map((id) => {
    const result = results?.find((item) => item.id === id);
    return {
      key: id,
      value: companyName(result, id),
      content: (
        <span className={styles.option}>
          <span className={styles.name}>{companyName(result, id)}</span>
          <span className={styles.meta}>
            {result?.error
              ? 'Профиль недоступен'
              : [result?.profile?.industry.okved_name, result?.profile?.location.region]
                  .filter(Boolean)
                  .join(' · ') || (loading ? 'Загружаем профиль…' : id)}
          </span>
        </span>
      ),
    };
  });

  return (
    <div className={styles.switcher}>
      <SelectDesktop
        size={48}
        block
        label="Демо-компания"
        labelView="inner"
        options={options}
        selected={company}
        optionsListWidth="field"
        valueRenderer={({ selected }) =>
          selected
            ? companyName(
                results?.find((item) => item.id === selected.key),
                selected.key,
              )
            : ''
        }
        onChange={({ selected }) => {
          if (selected) onChange(selected.key);
        }}
      />
    </div>
  );
}
