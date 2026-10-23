import { useId, useState } from 'react';
import { ArrowDownSIcon } from '@alfalab/icons-glyph/ArrowDownSIcon';
import { ArrowUpSIcon } from '@alfalab/icons-glyph/ArrowUpSIcon';
import { ChevronDownMIcon } from '@alfalab/icons-glyph/ChevronDownMIcon';

import type { Feed, FeedItem } from '@/shared/api/types';
import { formatSigned } from '@/shared/lib/format';

import styles from './ImpactCriteria.module.css';

type Impact = FeedItem['impact'];
type Criterion = Impact['criteria'][number];
type Answer = NonNullable<Impact['answers']>[number];
type CriteriaInfo = Feed['impact_criteria'];

const LEVEL_WORDS: Record<number, string> = {
  [-2]: 'существенно хуже',
  [-1]: 'заметно хуже',
  0: 'не затронуто',
  1: 'заметно лучше',
  2: 'существенно лучше',
};

const SOURCE_WORDS = { source: 'назван в материале', estimate: 'оценка ИИ' } as const;

export function affectedCriteria(impact: Impact): Criterion[] {
  return impact.criteria
    .filter((criterion) => criterion.level !== 0)
    .sort((a, b) => Math.abs(b.points) - Math.abs(a.points));
}

function levelWords(level: number): string {
  return LEVEL_WORDS[Math.max(-2, Math.min(2, level))] ?? '';
}

function toneClass(level: number): string {
  return level < 0 ? styles.downText : level > 0 ? styles.upText : styles.zeroText;
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
            <span className={toneClass(criterion.level)}> — {levelWords(criterion.level)}</span>
            {criterion.timing_title && (
              <span className={styles.meta}> · {criterion.timing_title}</span>
            )}
            {criterion.timing_source && (
              <span className={styles.meta}> · {SOURCE_WORDS[criterion.timing_source]}</span>
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

function Bar({ level }: { level: number }) {
  const value = Math.max(-2, Math.min(2, level));
  return (
    <span className={styles.bar} aria-hidden="true">
      {value !== 0 && (
        <span
          className={`${styles.fill} ${value < 0 ? styles.fillDown : styles.fillUp}`}
          style={{ width: `${(Math.abs(value) / 2) * 50}%` }}
        />
      )}
      <span className={styles.zero} />
    </span>
  );
}

function Timing({ item }: { item: Pick<Criterion, 'timing_title' | 'timing_source'> }) {
  if (!item.timing_title && !item.timing_source) return null;
  return (
    <span className={styles.chips}>
      {item.timing_title && <span className={styles.chip}>{item.timing_title}</span>}
      {item.timing_source && (
        <span
          className={`${styles.chip} ${
            item.timing_source === 'estimate' ? styles.estimate : styles.sourced
          }`}
        >
          срок: {SOURCE_WORDS[item.timing_source]}
        </span>
      )}
    </span>
  );
}

function Answers({ criterion, answers }: { criterion: Criterion; answers: Answer[] }) {
  if (answers.length === 0) {
    if (!criterion.reason) return null;
    return (
      <div className={styles.answers}>
        <div className={styles.answer}>
          <Timing item={criterion} />
          <p className={styles.reason}>{criterion.reason}</p>
        </div>
      </div>
    );
  }
  return (
    <ul className={styles.answers}>
      {answers.map((answer) => (
        <li key={answer.question_id} className={styles.answer}>
          <p className={styles.question}>
            {answer.question}{' '}
            <span className={toneClass(answer.level)}>{levelWords(answer.level)}</span>
          </p>
          <Timing item={answer} />
          <p className={styles.reason}>{answer.reason}</p>
        </li>
      ))}
    </ul>
  );
}

type BreakdownProps = { impact: Impact; info?: CriteriaInfo; compact?: boolean };

export function ImpactBreakdown({ impact, info, compact = false }: BreakdownProps) {
  const descriptions = new Map((info ?? []).map((item) => [item.id, item.description]));
  const answers = impact.answers ?? [];
  const touched = (criterion: Criterion) =>
    criterion.level !== 0 || answers.some((answer) => answer.criterion === criterion.id);
  const criteria = [...impact.criteria].sort(
    (a, b) => Number(touched(b)) - Number(touched(a)) || Math.abs(b.points) - Math.abs(a.points),
  );
  const contribution = (points: number) =>
    impact.scale === 'levels' ? `вклад ${formatSigned(points)}` : formatSigned(points);

  return (
    <ul className={`${styles.breakdown} ${compact ? styles.compact : ''}`}>
      {criteria.map((criterion) => {
        const own = answers
          .filter((answer) => answer.criterion === criterion.id)
          .sort((a, b) => Math.abs(b.level) - Math.abs(a.level) || a.level - b.level);
        const balanced = criterion.level === 0 && own.length > 0;
        return (
          <li
            key={criterion.id}
            className={`${styles.criterion} ${touched(criterion) ? '' : styles.idle}`}
          >
            <div className={styles.criterionHead}>
              <span className={styles.criterionTitle} title={descriptions.get(criterion.id)}>
                {criterion.title}
              </span>
              <Bar level={criterion.level} />
              {(!compact || touched(criterion)) && (
                <span className={`${styles.criterionLevel} ${toneClass(criterion.level)}`}>
                  {balanced
                    ? 'плюсы и минусы уравновесились'
                    : criterion.level === 0
                      ? levelWords(0)
                      : `${levelWords(criterion.level)} · ${contribution(criterion.points)}`}
                </span>
              )}
            </div>
            {touched(criterion) && <Answers criterion={criterion} answers={own} />}
          </li>
        );
      })}
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

export function CriteriaTable({ impact, info }: BreakdownProps) {
  return (
    <section className={styles.table} aria-labelledby="criteria-title">
      <div className={styles.tableHead}>
        <h2 id="criteria-title" className={styles.tableTitle}>
          Как скажется на бизнесе
        </h2>
        <span className={styles.total}>
          {impact.title} · итог {formatSigned(impact.score)}
          {impact.scale === 'points' && ' из ±100'}
          {impact.confidence === 'low' && ' · оценка неточная'}
        </span>
      </div>
      {(affectedCriteria(impact).length === 0 || impact.score === 0) && (
        <p className={styles.none}>{impact.rationale || 'Заметного влияния на показатели нет.'}</p>
      )}
      <ImpactBreakdown impact={impact} info={info} />
      <p className={styles.note}>
        {impact.scale === 'levels'
          ? 'Каждый вопрос оценивается от −2 до +2, итог — сумма всех оценок. Плюсы и минусы внутри критерия взаимно гасятся.'
          : 'Уровень критерия — самое сильное влияние среди вопросов его группы, поэтому один эффект не считается дважды.'}
      </p>
    </section>
  );
}
