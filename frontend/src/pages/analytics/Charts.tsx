import { useEffect, useRef, useState, type CSSProperties } from 'react';

import { formatSigned } from '@/shared/lib/format';

import {
  DIRECTION_TITLES,
  type Analytics,
  type Day,
  type Direction,
  type Distribution,
  type Selection,
} from './model';
import styles from './AnalyticsPage.module.css';

type Select = { selection: Selection | null; onSelect: (selection: Selection) => void };

export function DailyImpact({ data, selection, onSelect }: { data: Analytics } & Select) {
  const [hovered, setHovered] = useState<Day | null>(null);
  const plot = useRef<HTMLDivElement>(null);
  const endKey = data.days.at(-1)?.key;
  useEffect(() => {
    const element = plot.current;
    if (!element) return;
    const showLatest = () => {
      element.scrollLeft = element.scrollWidth - element.clientWidth;
    };
    showLatest();
    const observer = new ResizeObserver(showLatest);
    observer.observe(element);
    return () => observer.disconnect();
  }, [endKey, data.days.length]);
  const max = Math.max(1, ...data.days.flatMap((day) => [day.risk, day.opportunity]));
  const unit = data.units === 'points' ? 'баллов' : 'событий';

  return (
    <section className={`${styles.panel} ${styles.daily}`} aria-labelledby="daily-title">
      <div className={styles.panelHead}>
        <div>
          <h2 id="daily-title">Как события влияли на бизнес</h2>
          <p className={styles.caption}>
            {data.units === 'points'
              ? 'Сумма оценок по датам публикации'
              : 'Количество событий с рисками и возможностями'}
          </p>
        </div>
        <div className={styles.legend}>
          <span>
            <i className={styles.positiveDot} />
            Возможности
          </span>
          <span>
            <i className={styles.negativeDot} />
            Риски
          </span>
        </div>
      </div>
      <p className={styles.caption}>
        {data.days[0]?.fullLabel} — {data.days.at(-1)?.fullLabel} · МСК
      </p>
      <div ref={plot} className={styles.plot}>
        <div className={styles.axis} aria-hidden="true">
          <span>+{max}</span>
          <span>0</span>
          <span>−{max}</span>
        </div>
        <div
          className={styles.columns}
          style={{ minWidth: Math.max(240, data.days.length * 30) }}
          role="group"
          aria-label={`Влияние по дням, ${unit}`}
        >
          <div className={styles.gridlines} aria-hidden="true">
            <span />
            <span />
            <span />
          </div>
          {data.days.map((day) => (
            <button
              key={day.key}
              className={styles.day}
              type="button"
              aria-label={`${day.fullLabel}: возможности ${day.opportunity}, риски ${day.risk}, событий ${day.count}`}
              aria-pressed={selection?.kind === 'day' && selection.key === day.key}
              onMouseEnter={() => setHovered(day)}
              onMouseLeave={() => setHovered(null)}
              onFocus={() => setHovered(day)}
              onBlur={() => setHovered(null)}
              onClick={() => onSelect({ kind: 'day', key: day.key, title: day.fullLabel })}
            >
              <span className={styles.barArea} aria-hidden="true">
                <span
                  className={styles.positiveBar}
                  style={{ height: `${(day.opportunity / max) * 44}%` }}
                />
                <span
                  className={styles.negativeBar}
                  style={{ height: `${(day.risk / max) * 44}%` }}
                />
              </span>
              <span className={styles.dayLabel}>{day.label}</span>
            </button>
          ))}
        </div>
      </div>
      <p className={styles.chartHint} role="status">
        {hovered
          ? `${hovered.fullLabel} · возможности ${formatSigned(hovered.opportunity)}, риски ${formatSigned(-hovered.risk)} ${unit} · ${hovered.count} событий`
          : 'Нажмите на день, чтобы увидеть его события ниже.'}
      </p>
      <details className={styles.tableToggle}>
        <summary>Показать таблицей</summary>
        <div className={styles.tableScroll}>
          <table>
            <caption className={styles.srOnly}>Влияние по дням</caption>
            <thead>
              <tr>
                <th>Дата</th>
                <th>События</th>
                <th>Возможности</th>
                <th>Риски</th>
              </tr>
            </thead>
            <tbody>
              {data.days.map((day) => (
                <tr key={day.key}>
                  <th scope="row">{day.fullLabel}</th>
                  <td>{day.count}</td>
                  <td className={styles.up}>{formatSigned(day.opportunity)}</td>
                  <td className={styles.down}>{formatSigned(-day.risk)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
      {data.unplotted > 0 && (
        <p className={styles.caption}>Вне временной шкалы: {data.unplotted} событий.</p>
      )}
    </section>
  );
}

export function EventBalance({ data, selection, onSelect }: { data: Analytics } & Select) {
  const total = data.items.length;
  let start = 0;
  const segments = (Object.keys(DIRECTION_TITLES) as Direction[]).map((key) => {
    const share = total ? (data.directions[key] / total) * 100 : 0;
    const segment = `var(--tone-${key}) ${start}% ${start + share}%`;
    start += share;
    return segment;
  });
  return (
    <section className={`${styles.panel} ${styles.balance}`} aria-labelledby="balance-title">
      <div>
        <h2 id="balance-title">Баланс событий</h2>
        <p className={styles.caption}>Характер влияния на вашу компанию</p>
      </div>
      <div
        className={styles.donut}
        style={
          {
            '--segments': total ? segments.join(',') : 'var(--c-bg-tertiary) 0% 100%',
          } as CSSProperties
        }
        role="img"
        aria-label={(Object.keys(DIRECTION_TITLES) as Direction[])
          .map((key) => `${DIRECTION_TITLES[key]}: ${data.directions[key]}`)
          .join(', ')}
      >
        <div>
          <strong>{total}</strong>
          <span>событий</span>
        </div>
      </div>
      <div className={styles.balanceLegend}>
        {(Object.keys(DIRECTION_TITLES) as Direction[]).map((key) => (
          <button
            type="button"
            key={key}
            aria-pressed={selection?.kind === 'direction' && selection.key === key}
            onClick={() => onSelect({ kind: 'direction', key, title: DIRECTION_TITLES[key] })}
          >
            <i style={{ background: `var(--tone-${key})` }} aria-hidden="true" />
            <span>{DIRECTION_TITLES[key]}</span>
            <strong>{data.directions[key]}</strong>
          </button>
        ))}
      </div>
    </section>
  );
}

export function DistributionChart({
  title,
  description,
  groups,
  kind,
  total,
  selection,
  onSelect,
}: {
  title: string;
  description: string;
  groups: Distribution[];
  kind: 'marker' | 'topic';
  total: number;
} & Select) {
  const [expanded, setExpanded] = useState(false);
  const max = Math.max(1, ...groups.map((group) => group.count));
  return (
    <section className={styles.panel} aria-label={title}>
      <div>
        <h2>{title}</h2>
        <p className={styles.caption}>{description}</p>
      </div>
      <div className={styles.distribution}>
        {(expanded ? groups : groups.slice(0, 6)).map((group, index) => (
          <button
            key={group.key}
            type="button"
            className={styles.distributionRow}
            aria-pressed={selection?.kind === kind && selection.key === group.key}
            onClick={() => onSelect({ kind, key: group.key, title: group.title })}
          >
            {kind === 'topic' && <span className={styles.topicRank}>{index + 1}</span>}
            <span className={styles.barContent}>
              <span className={styles.distributionLabel}>
                {group.title}
                <strong>
                  {group.count}
                  <span> · {Math.round((group.count / Math.max(1, total)) * 100)}%</span>
                </strong>
              </span>
              <span className={styles.track}>
                <span
                  style={{ width: `${(group.count / max) * 100}%` }}
                  className={kind === 'topic' ? styles.topicBar : styles.markerBar}
                />
              </span>
            </span>
          </button>
        ))}
      </div>
      {groups.length === 0 && <p className={styles.caption}>Пока нет данных для распределения.</p>}
      {groups.length > 6 && (
        <button className={styles.textButton} type="button" onClick={() => setExpanded(!expanded)}>
          {expanded ? 'Свернуть' : `Показать все · ${groups.length}`}
        </button>
      )}
    </section>
  );
}

export function MiniBars({ days, kind }: { days: Day[]; kind: 'count' | 'risk' | 'opportunity' }) {
  const max = Math.max(1, ...days.map((day) => day[kind]));
  return (
    <span className={styles.miniBars} aria-hidden="true">
      {days.map((day) => (
        <i
          key={day.key}
          style={{ height: `${day[kind] > 0 ? Math.max(8, (day[kind] / max) * 100) : 0}%` }}
        />
      ))}
    </span>
  );
}
