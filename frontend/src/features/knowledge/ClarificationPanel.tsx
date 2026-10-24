import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Link } from 'react-router';
import { ButtonDesktop } from '@alfalab/core-components-button/desktop';
import { BulbMIcon } from '@alfalab/icons-glyph/BulbMIcon';
import { CheckmarkMIcon } from '@alfalab/icons-glyph/CheckmarkMIcon';
import { ArrowRightMIcon } from '@alfalab/icons-glyph/ArrowRightMIcon';

import { ApiError, apiPost, errorMessage, isAbort } from '@/shared/api/client';
import { useApi } from '@/shared/api/useApi';
import { plural } from '@/shared/lib/format';
import { ErrorState, Skeleton } from '@/shared/ui/StateView';

import {
  factAnchor,
  isAnswerFor,
  questionPath,
  quickAnswers,
  textAnswer,
  unansweredQuestions,
  questionTitle,
  type Answer,
  type KnowledgeFact,
  type KnowledgeGaps,
} from './model';
import styles from './ClarificationPanel.module.css';

type Props = {
  company: string;
  search: string;
  facts?: KnowledgeFact[];
  onAnswered: (fact: KnowledgeFact) => void;
  compact?: boolean;
};

export function ClarificationPanel(props: Props) {
  return <CompanyQuestions key={props.company} {...props} />;
}

function CompanyQuestions({ company, search, facts = [], onAnswered, compact = false }: Props) {
  const gaps = useApi<KnowledgeGaps>(questionPath(company));
  const [completed, setCompleted] = useState<string[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [writing, setWriting] = useState(false);
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string>();
  const [lastAnswer, setLastAnswer] = useState<KnowledgeFact>();
  const request = useRef<AbortController | null>(null);
  const lastAttempt = useRef<{ body: Answer; action: string } | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const done = useRef<HTMLParagraphElement>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const restoreFocus = useRef(false);
  useEffect(() => () => request.current?.abort(), []);

  const questions = unansweredQuestions(gaps.data?.questions ?? [], facts, completed);
  const question = questions.find((item) => item.key === selected) ?? questions[0];
  const index = question ? questions.indexOf(question) : 0;
  const draft = question && Object.hasOwn(drafts, question.key) ? drafts[question.key] : '';
  const body = textAnswer(draft);
  const length = Array.from(draft.trim()).length;
  const options = question ? quickAnswers(question.key) : [];
  const showInput = writing || Boolean(draft) || options.length === 0;
  const knowledgeLink = {
    pathname: '/knowledge',
    search,
    hash: lastAnswer ? `#${encodeURIComponent(factAnchor(lastAnswer.id))}` : '#clarify',
  };
  useEffect(() => {
    if (restoreFocus.current) {
      (heading.current ?? done.current)?.focus();
      restoreFocus.current = false;
    }
  }, [question?.key]);
  useEffect(() => {
    if (writing) textarea.current?.focus();
  }, [writing]);

  const choose = (key: string) => {
    if (request.current) return;
    setSelected(key);
    setWriting(false);
    setError(undefined);
    lastAttempt.current = null;
  };
  const answer = async (answerBody: Answer, action: string) => {
    if (!question || request.current) return;
    const controller = new AbortController();
    request.current = controller;
    lastAttempt.current = { body: answerBody, action };
    setPending(action);
    setError(undefined);
    try {
      const fact = await apiPost<KnowledgeFact>(
        questionPath(company, question.key),
        answerBody,
        controller.signal,
      );
      controller.signal.throwIfAborted();
      if (!isAnswerFor(fact, company, question.key))
        throw new ApiError('Сервер вернул ответ для другого вопроса. Повторите сохранение.', 502);
      onAnswered(fact);
      setLastAnswer(fact);
      setCompleted((keys) => [...keys, question.key]);
      setDrafts((current) => {
        const next = { ...current };
        delete next[question.key];
        return next;
      });
      setSelected(null);
      setWriting(false);
      restoreFocus.current = true;
      lastAttempt.current = null;
    } catch (caught) {
      if (!isAbort(caught)) setError(errorMessage(caught));
    } finally {
      if (!controller.signal.aborted) {
        setPending(null);
        request.current = null;
      }
    }
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (body) void answer(body, 'text');
  };

  return (
    <section
      className={styles.panel}
      id="clarify"
      tabIndex={-1}
      aria-label="Помогите уточнить"
      data-compact={compact}
    >
      <div className={styles.header}>
        <div className={styles.intro}>
          <span className={styles.icon}>
            <BulbMIcon aria-hidden="true" />
          </span>
          <div>
            <h2>Помогите уточнить</h2>
            <p>Вы знаете свой бизнес лучше. Ответы сделают подборку точнее.</p>
          </div>
        </div>
        {gaps.data && questions.length > 0 && (
          <span className={styles.count}>
            {questions.length}{' '}
            {plural(questions.length, { one: 'вопрос', few: 'вопроса', many: 'вопросов' })}
          </span>
        )}
      </div>
      {gaps.loading && (
        <div role="status">
          <p className={styles.hint}>Подбираем вопросы о компании…</p>
          <Skeleton lines={2} />
        </div>
      )}
      {gaps.error && (
        <ErrorState
          title="Не удалось загрузить вопросы"
          message={gaps.error}
          onRetry={gaps.retry}
        />
      )}
      <div className={styles.saved} role="status" aria-live="polite">
        {lastAnswer && (
          <>
            <CheckmarkMIcon aria-hidden="true" />
            <div>
              <strong>Ответ добавлен в базу знаний</strong>
              <p>{lastAnswer.text}</p>
              <Link to={knowledgeLink}>Посмотреть факт</Link>
            </div>
          </>
        )}
      </div>
      {gaps.data &&
        (question ? (
          <div className={styles.question}>
            <div className={styles.questionHead}>
              <label className={styles.topic}>
                <span className={styles.srOnly}>Тема уточнения</span>
                <select
                  aria-label="Тема уточнения"
                  value={question.key}
                  onChange={(event) => choose(event.target.value)}
                  disabled={pending !== null}
                >
                  {questions.map((item) => (
                    <option key={item.key} value={item.key}>
                      {questionTitle(item)}
                    </option>
                  ))}
                </select>
              </label>
              <span className={styles.hint}>
                {index + 1} из {questions.length}
              </span>
            </div>
            <h3 ref={heading} tabIndex={-1}>
              {question.question}
            </h3>
            <p className={styles.why}>{question.why}</p>
            {options.length > 0 && (
              <div className={styles.options} role="group" aria-label="Быстрый ответ">
                {options.map((option, optionIndex) => (
                  <ButtonDesktop
                    key={option.title}
                    view="secondary"
                    size={40}
                    disabled={pending !== null}
                    loading={pending === `quick-${optionIndex}`}
                    onClick={() => void answer(option.body, `quick-${optionIndex}`)}
                  >
                    {option.title}
                  </ButtonDesktop>
                ))}
              </div>
            )}
            {showInput ? (
              <form
                className={styles.form}
                onSubmit={submit}
                aria-label="Ответ на уточняющий вопрос"
              >
                <label>
                  <span>Ваш ответ</span>
                  <textarea
                    ref={textarea}
                    rows={compact ? 2 : 3}
                    value={draft}
                    disabled={pending !== null}
                    onChange={(event) =>
                      setDrafts((current) => ({ ...current, [question.key]: event.target.value }))
                    }
                    placeholder="Напишите, как обстоят дела в вашей компании"
                    aria-invalid={length > 2000}
                    required
                  />
                </label>
                <div className={styles.formFoot}>
                  <span className={styles.hint} data-invalid={length > 2000}>
                    {length} / 2000
                  </span>
                  <ButtonDesktop
                    type="submit"
                    size={40}
                    view="primary"
                    disabled={!body || pending !== null}
                    loading={pending === 'text'}
                  >
                    Сохранить ответ
                  </ButtonDesktop>
                </div>
              </form>
            ) : (
              <button
                className={styles.textButton}
                type="button"
                onClick={() => setWriting(true)}
                disabled={pending !== null}
              >
                Ответить текстом
              </button>
            )}
            {error && (
              <ErrorState
                title="Не удалось сохранить ответ"
                message={error}
                onRetry={
                  pending === null && lastAttempt.current
                    ? () => {
                        const attempt = lastAttempt.current;
                        if (attempt) void answer(attempt.body, attempt.action);
                      }
                    : undefined
                }
              />
            )}
            <div className={styles.footer}>
              <button
                type="button"
                className={styles.textButton}
                onClick={() => void answer({ not_applicable: true }, 'skip')}
                disabled={pending !== null}
              >
                {pending === 'skip' ? 'Сохраняем…' : 'Не про нас'}
              </button>
              {questions.length > 1 && (
                <button
                  className={styles.next}
                  type="button"
                  disabled={pending !== null}
                  onClick={() => choose(questions[(index + 1) % questions.length].key)}
                >
                  Другой вопрос
                  <ArrowRightMIcon aria-hidden="true" />
                </button>
              )}
            </div>
          </div>
        ) : (
          <p className={styles.done} ref={done} tabIndex={-1}>
            <CheckmarkMIcon aria-hidden="true" />
            Сейчас уточнений больше нет. Факты можно дополнить в базе знаний.
          </p>
        ))}
      {compact && (
        <Link className={styles.allLink} to={{ pathname: '/knowledge', search, hash: '#clarify' }}>
          Перейти в базу знаний
          <ArrowRightMIcon aria-hidden="true" />
        </Link>
      )}
    </section>
  );
}
