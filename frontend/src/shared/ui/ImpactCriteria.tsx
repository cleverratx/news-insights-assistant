import { useId, useState } from 'react';
import { ArrowDownSIcon } from '@alfalab/icons-glyph/ArrowDownSIcon';
import { ArrowUpSIcon } from '@alfalab/icons-glyph/ArrowUpSIcon';
import { ChevronDownMIcon } from '@alfalab/icons-glyph/ChevronDownMIcon';

import type { Feed, FeedItem } from '@/shared/api/types';
import { formatSigned, plural } from '@/shared/lib/format';

import styles from './ImpactCriteria.module.css';

type Forms = { one: string; few: string; many: string };

function counted(count: number, forms: Forms): string {
  return `${count}\u00a0${plural(count, forms)}`;
}

type Impact = FeedItem['impact'];
type Criterion = Impact['criteria'][number];
type Answer = NonNullable<Impact['answers']>[number];
type CriteriaInfo = Feed['impact_criteria'];
type Timed = Pick<Criterion, 'timing_title' | 'timing_source'>;

const LEVEL_WORDS: Record<number, string> = {
  [-2]: 'существенно хуже',
  [-1]: 'заметно хуже',
  0: 'не затронуто',
  1: 'заметно лучше',
  2: 'существенно лучше',
};

const SOURCE_WORDS = { source: 'срок из материала', estimate: 'оценка ИИ' } as const;

const NOTES: Record<Impact['scale'], string> = {
  weighted:
    'Ответ «заметно» даёт до 30, «существенно» — до 60. Меньше, если скажется не скоро или ваши данные этот канал не подтверждают. Риски и возможности копятся до 100, итог — возможности минус риски.',
  levels:
    'Каждый вопрос оценивается от −2 до +2, итог — сумма всех оценок. Плюсы и минусы внутри критерия взаимно гасятся.',
  points:
    'Уровень критерия — самое сильное влияние среди вопросов его группы, поэтому один эффект не считается дважды.',
};

export function affectedCriteria(impact: Impact): Criterion[] {
  return impact.criteria
    .filter((criterion) => criterion.level !== 0)
    .sort((a, b) => Math.abs(b.points) - Math.abs(a.points));
}

function levelWords(level: number): string {
  return LEVEL_WORDS[Math.max(-2, Math.min(2, level))] ?? '';
}

function toneClass(value: number): string {
  return value < 0 ? styles.downText : value > 0 ? styles.upText : styles.zeroText;
}

function weighted(impact: Pick<Impact, 'scale'>): boolean {
  return impact.scale === 'weighted';
}

function touchedCriteria(impact: Impact): Criterion[] {
  const answers = impact.answers ?? [];
  return impact.criteria
    .filter(
      (criterion) =>
        criterion.level !== 0 || answers.some((answer) => answer.criterion === criterion.id),
    )
    .sort((a, b) => Math.abs(b.points) - Math.abs(a.points));
}

function timingText(item: Timed): string {
  return [item.timing_title, item.timing_source && SOURCE_WORDS[item.timing_source]]
    .filter(Boolean)
    .join(' · ');
}

function Arrow({ level }: { level: number }) {
  const Icon = level < 0 ? ArrowDownSIcon : ArrowUpSIcon;
  return (
    <Icon className={`${styles.arrow} ${level < 0 ? styles.down : styles.up}`} aria-hidden="true" />
  );
}

export function CriteriaLine({ impact, limit = 3 }: { impact: Impact; limit?: number }) {
  const criteria = affectedCriteria(impact);
  if (criteria.length === 0) return null;
  const shown = criteria.slice(0, limit);

  return (
    <ul className={styles.line} aria-label="Как влияет на бизнес">
      {shown.map((criterion) => (
        <li key={criterion.id} className={styles.lineItem}>
          <Arrow level={criterion.level} />
          <span>
            <span className={styles.title}>{criterion.title}</span>
            <span className={toneClass(criterion.level)}>
              {' '}
              — {levelWords(criterion.level)}
              {weighted(impact) && ` ${formatSigned(criterion.points)}`}
            </span>
            {timingText(criterion) && (
              <span className={styles.meta}> · {timingText(criterion)}</span>
            )}
          </span>
        </li>
      ))}
      {criteria.length > shown.length && (
        <li className={styles.more}>и ещё {criteria.length - shown.length}</li>
      )}
    </ul>
  );
}

function Bar({ value, share }: { value: number; share: number }) {
  return (
    <span className={styles.bar} aria-hidden="true">
      {value !== 0 && share > 0 && (
        <span
          className={`${styles.fill} ${value < 0 ? styles.fillDown : styles.fillUp}`}
          style={{ width: `${Math.min(share, 1) * 50}%` }}
        />
      )}
      <span className={styles.zero} />
    </span>
  );
}

function Timing({ item }: { item: Timed }) {
  const text = timingText(item);
  if (!text) return null;
  return (
    <span
      className={`${styles.chip} ${item.timing_source === 'source' ? styles.sourced : styles.estimate}`}
    >
      {text}
    </span>
  );
}

function Exposure({ answer }: { answer: Answer }) {
  const features = answer.features ?? [];
  if (features.length === 0) {
    return <span className={styles.chip}>ваши данные этот канал не подтверждают</span>;
  }
  const full = features.map((feature) => feature.label).join('; ');
  const short = features.map((feature) => feature.label.split(/[:(]/)[0]?.trim() || feature.label);
  return (
    <span className={`${styles.chip} ${styles.yours}`} title={full}>
      по вашим данным: {short.join(', ')}
    </span>
  );
}

type AnswersProps = { id: string; criterion: Criterion; answers: Answer[]; scored: boolean };

function Answers({ id, criterion, answers, scored }: AnswersProps) {
  if (answers.length === 0) {
    return (
      <div id={id} className={styles.answers}>
        <div className={styles.answer}>
          <span className={styles.chips}>
            <Timing item={criterion} />
          </span>
          {criterion.reason && <p className={styles.reason}>{criterion.reason}</p>}
        </div>
      </div>
    );
  }
  return (
    <ul id={id} className={styles.answers}>
      {answers.map((answer) => (
        <li key={answer.question_id} className={styles.answer}>
          <p className={styles.question}>
            {answer.question}{' '}
            <span className={toneClass(answer.level)}>
              {levelWords(answer.level)}
              {scored && answer.points != null && ` · ${formatSigned(answer.points)}`}
            </span>
          </p>
          <span className={styles.chips}>
            <Timing item={answer} />
            {scored && <Exposure answer={answer} />}
          </span>
          <p className={styles.reason}>{answer.reason}</p>
        </li>
      ))}
    </ul>
  );
}

type RowProps = {
  impact: Impact;
  criterion: Criterion;
  description?: string;
};

function CriterionRow({ impact, criterion, description }: RowProps) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const scored = weighted(impact);
  const own = (impact.answers ?? [])
    .filter((answer) => answer.criterion === criterion.id)
    .sort(
      (a, b) =>
        Math.abs(b.points ?? 0) - Math.abs(a.points ?? 0) ||
        Math.abs(b.level) - Math.abs(a.level) ||
        a.level - b.level,
    );
  const balanced = criterion.level === 0 && own.length > 0;
  const value = criterion.points || criterion.level;
  const share = scored ? Math.abs(criterion.points) / 100 : Math.abs(criterion.level) / 2;
  const contribution =
    impact.scale === 'levels'
      ? `вклад ${formatSigned(criterion.points)}`
      : formatSigned(criterion.points);
  const count = own.length || 1;

  return (
    <li className={styles.criterion}>
      <button
        type="button"
        className={styles.criterionHead}
        aria-expanded={open}
        aria-controls={id}
        title={description}
        onClick={() => setOpen((current) => !current)}
      >
        <span className={styles.criterionTitle}>
          {criterion.title}
          <span className={styles.count}>
            {counted(count, { one: 'вопрос', few: 'вопроса', many: 'вопросов' })}
          </span>
        </span>
        <Bar value={value} share={share} />
        <span className={`${styles.criterionLevel} ${toneClass(value)}`}>
          {balanced
            ? 'плюсы и минусы уравновесились'
            : `${levelWords(criterion.level)} · ${contribution}`}
        </span>
        <ChevronDownMIcon
          className={`${styles.chevron} ${open ? styles.chevronOpen : ''}`}
          aria-hidden="true"
        />
      </button>
      {open && <Answers id={id} criterion={criterion} answers={own} scored={scored} />}
    </li>
  );
}

type BreakdownProps = { impact: Impact; info?: CriteriaInfo; compact?: boolean };

export function ImpactBreakdown({ impact, info, compact = false }: BreakdownProps) {
  const descriptions = new Map((info ?? []).map((item) => [item.id, item.description]));
  const criteria = touchedCriteria(impact);
  if (criteria.length === 0) {
    return <p className={styles.none}>Заметного влияния на показатели нет.</p>;
  }

  return (
    <ul className={`${styles.breakdown} ${compact ? styles.compact : ''}`}>
      {criteria.map((criterion) => (
        <CriterionRow
          key={criterion.id}
          impact={impact}
          criterion={criterion}
          description={descriptions.get(criterion.id)}
        />
      ))}
    </ul>
  );
}

export function ImpactToggle({ impact, info }: BreakdownProps) {
  const [open, setOpen] = useState(false);
  const id = useId();

  return (
    <div className={styles.toggleWrap}>
      <button
        type="button"
        className={styles.toggle}
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((value) => !value)}
      >
        Влияние по критериям
        <ChevronDownMIcon
          className={`${styles.chevron} ${open ? styles.chevronOpen : ''}`}
          aria-hidden="true"
        />
      </button>
      {open && (
        <div id={id} className={styles.panel}>
          <ImpactBreakdown impact={impact} info={info} compact />
        </div>
      )}
    </div>
  );
}

function sideCounts(impact: Impact): [number, number] {
  const parts = (impact.answers ?? []).length
    ? (impact.answers ?? []).map((answer) => answer.points ?? answer.level)
    : impact.criteria.map((criterion) => criterion.points);
  return [parts.filter((part) => part < 0).length, parts.filter((part) => part > 0).length];
}

function Balance({ impact }: { impact: Impact }) {
  const risk = impact.risk ?? 0;
  const opportunity = impact.opportunity ?? 0;
  const [risks, chances] = sideCounts(impact);
  const score = impact.score;
  const label =
    `Риски ${risk} из 100 (${counted(risks, { one: 'ответ', few: 'ответа', many: 'ответов' })}), ` +
    `возможности ${opportunity} из 100 (${counted(chances, { one: 'ответ', few: 'ответа', many: 'ответов' })}), ` +
    `итог ${formatSigned(score)}`;

  return (
    <div className={styles.balance} role="img" aria-label={label}>
      <span className={styles.balanceTrack} aria-hidden="true">
        <span className={styles.riskSide} style={{ width: `${risk / 2}%` }} />
        <span className={styles.chanceSide} style={{ width: `${opportunity / 2}%` }} />
        {score !== 0 && (
          <span
            className={`${styles.net} ${score < 0 ? styles.netDown : styles.netUp}`}
            style={{ width: `${Math.min(Math.abs(score), 100) / 2}%` }}
          />
        )}
        <span className={styles.zero} />
      </span>
      <span className={styles.balanceLegend} aria-hidden="true">
        <span className={styles.legendRisk}>
          <ArrowDownSIcon className={`${styles.arrow} ${styles.down}`} />
          <b>{risk > 0 ? formatSigned(-risk) : '0'}</b>
          <span>{counted(risks, { one: 'риск', few: 'риска', many: 'рисков' })}</span>
        </span>
        <span className={`${styles.legendNet} ${toneClass(score)}`}>
          итог <b>{formatSigned(score)}</b>
        </span>
        <span className={styles.legendChance}>
          <span>
            {counted(chances, { one: 'возможность', few: 'возможности', many: 'возможностей' })}
          </span>
          <b>{formatSigned(opportunity)}</b>
          <ArrowUpSIcon className={`${styles.arrow} ${styles.up}`} />
        </span>
      </span>
    </div>
  );
}

export function CriteriaTable({ impact, info }: BreakdownProps) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const criteria = touchedCriteria(impact);

  return (
    <section className={styles.table} aria-labelledby="criteria-title">
      <div className={styles.tableHead}>
        <h2 id="criteria-title" className={styles.tableTitle}>
          Как скажется на бизнесе
        </h2>
        <span className={styles.total}>
          {impact.title} · итог {formatSigned(impact.score)}
          {impact.scale !== 'levels' && ' из ±100'}
          {impact.confidence === 'low' && ' · оценка неточная'}
        </span>
      </div>
      {weighted(impact) && <Balance impact={impact} />}
      {criteria.length === 0 ? (
        <p className={styles.none}>{impact.rationale || 'Заметного влияния на показатели нет.'}</p>
      ) : (
        <>
          <button
            type="button"
            className={styles.toggle}
            aria-expanded={open}
            aria-controls={id}
            onClick={() => setOpen((value) => !value)}
          >
            {open ? 'Скрыть критерии' : 'Из чего складывается оценка'}
            <span className={styles.toggleCount}>
              {counted(criteria.length, { one: 'критерий', few: 'критерия', many: 'критериев' })}
            </span>
            <ChevronDownMIcon
              className={`${styles.chevron} ${open ? styles.chevronOpen : ''}`}
              aria-hidden="true"
            />
          </button>
          {open && (
            <div id={id} className={styles.tableBody}>
              <ImpactBreakdown impact={impact} info={info} />
              <p className={styles.note}>{NOTES[impact.scale]}</p>
            </div>
          )}
        </>
      )}
    </section>
  );
}
