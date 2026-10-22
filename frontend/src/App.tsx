import { useRef, useState } from 'react';
import './app.css';

type Card = { title: string; image?: string; url?: string };
type Impact = { score: number; color: string; rationale: string; method: string };
type Entry = {
  news_id: string;
  card: Card;
  summary?: string;
  importance?: { score: number; title: string };
  impact?: Impact;
};
type Feed = {
  company_id: string;
  snapshot_id?: string;
  role?: string;
  items: Entry[];
  warnings?: string[];
};
type Statement = { id: string; text: string; origin: string; source_ids: string[] };
type Source = { id: string; title: string; url?: string; quote?: string };
type Ranking = { news_id: string; importance: { score: number; title: string }; impact: Impact };
type Analysis = {
  insight: { id: string; headline: string; status: string; status_reason?: string };
  presentation: {
    facts: { statement: Statement; label: string }[];
    bank_position: { statement: Statement; label: string }[];
    ai_conclusions: { statement: Statement; label: string }[];
    client_context: { statement: Statement; label: string }[];
    blocks: { id: string; title: string; statement_ids: string[]; empty_reason?: string }[];
    originals: Source[];
  };
  ranking: Ranking[];
  warnings: string[];
};
type Trace = {
  execution_status: string;
  steps: { number: number; goal: string; tool?: string; status: string }[];
};

async function request<T>(url: string, body?: object): Promise<T> {
  const response = await fetch(
    url,
    body
      ? {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        }
      : undefined,
  );
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(
      typeof error.detail === 'string' ? error.detail : `Ошибка запроса (${response.status})`,
    );
  }
  return response.json() as Promise<T>;
}

export function App() {
  const [company, setCompany] = useState('');
  const [role, setRole] = useState('');
  const [view, setView] = useState('feed');
  const [background, setBackground] = useState(false);
  const [feed, setFeed] = useState<Feed | null>(null);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [trace, setTrace] = useState<Trace | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const version = useRef(0);
  const reset = () => {
    version.current++;
    setFeed(null);
    setAnalysis(null);
    setTrace(null);
    setBusy(false);
    setError('');
  };

  async function load() {
    const current = ++version.current;
    setBusy(true);
    setError('');
    setFeed(null);
    setAnalysis(null);
    setTrace(null);
    try {
      const route = view === 'feed' ? `feed?role=${encodeURIComponent(role)}` : 'related-feed';
      let cleanRoute = role || view !== 'feed' ? route : 'feed';
      if (background && view === 'feed')
        cleanRoute += `${cleanRoute.includes('?') ? '&' : '?'}min_importance=0`;
      const result = await request<Feed>(
        `/api/companies/${encodeURIComponent(company.trim())}/${cleanRoute}`,
      );
      if (current === version.current) setFeed(result);
    } catch (e) {
      if (current === version.current) setError(String(e));
    } finally {
      if (current === version.current) setBusy(false);
    }
  }

  async function open(entry: Entry) {
    if (!feed) return;
    const current = ++version.current;
    setBusy(true);
    setError('');
    setAnalysis(null);
    setTrace(null);
    try {
      const result = await request<Analysis>(
        `/api/companies/${encodeURIComponent(feed.company_id)}/insights`,
        {
          news_ids: [entry.news_id],
          role: role || null,
          feed_snapshot_id: view === 'feed' ? feed.snapshot_id : null,
        },
      );
      if (current === version.current) setAnalysis(result);
    } catch (e) {
      if (current === version.current) setError(String(e));
    } finally {
      if (current === version.current) setBusy(false);
    }
  }

  async function showTrace() {
    if (!analysis) return;
    const current = version.current;
    setBusy(true);
    setError('');
    try {
      const result = await request<Trace>(
        `/api/insights/${encodeURIComponent(analysis.insight.id)}/trace`,
      );
      if (current === version.current) setTrace(result);
    } catch (e) {
      if (current === version.current) setError(String(e));
    } finally {
      if (current === version.current) setBusy(false);
    }
  }

  const rows = analysis
    ? [
        ...analysis.presentation.facts,
        ...analysis.presentation.bank_position,
        ...analysis.presentation.ai_conclusions,
        ...analysis.presentation.client_context,
      ]
    : [];
  const statements = new Map(rows.map((row) => [row.statement.id, row]));
  return (
    <main>
      <h1>Новостная аналитика</h1>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void load();
        }}
      >
        <label>
          Компания
          <input
            value={company}
            placeholder="SYNTHETIC_U"
            required
            onChange={(e) => {
              reset();
              setCompany(e.target.value);
            }}
          />
        </label>
        <label>
          Должность
          <select
            value={role}
            onChange={(e) => {
              reset();
              setRole(e.target.value);
            }}
          >
            <option value="">Вся компания</option>
            <option value="ceo">Руководитель</option>
            <option value="cfo">Финансовый директор</option>
            <option value="logistics">Логистика</option>
          </select>
        </label>
        <label>
          Новости
          <select
            value={view}
            onChange={(e) => {
              reset();
              setView(e.target.value);
            }}
          >
            <option value="feed">Для компании и должности</option>
            <option value="related">Конкуренты и контрагенты</option>
          </select>
        </label>
        <label>
          <span>Региональный фон</span>
          <input
            type="checkbox"
            checked={background}
            onChange={(e) => {
              reset();
              setBackground(e.target.checked);
            }}
          />
          Показывать новости низкой важности
        </label>
        <button disabled={busy || !company.trim()}>Загрузить</button>
      </form>
      {busy && <p role="status">Выполняется запрос…</p>}
      {error && <p role="alert">{error}</p>}
      {feed?.warnings?.map((warning, i) => (
        <p key={i}>{warning}</p>
      ))}
      {feed && feed.items.length === 0 && <p>За выбранный период новости не найдены.</p>}
      <div className="cards">
        {feed?.items.map((entry) => (
          <article key={entry.news_id}>
            {entry.card.image && <img src={entry.card.image} alt="" loading="lazy" />}
            <h2>{entry.card.title}</h2>
            <p>{entry.summary}</p>
            {entry.importance && (
              <p>
                {entry.importance.title}: {entry.importance.score}/10
              </p>
            )}
            {entry.impact && (
              <p className={entry.impact.color}>
                Влияние: {entry.impact.score > 0 ? '+' : ''}
                {entry.impact.score} — {entry.impact.rationale}
              </p>
            )}
            <button disabled={busy} onClick={() => void open(entry)}>
              Разобрать событие
            </button>
            {entry.card.url && (
              <p>
                <a href={entry.card.url} target="_blank" rel="noreferrer">
                  Оригинальная статья
                </a>
              </p>
            )}
          </article>
        ))}
      </div>
      {analysis && (
        <section>
          <h2>{analysis.insight.headline}</h2>
          {analysis.insight.status_reason && <p>{analysis.insight.status_reason}</p>}
          {analysis.ranking.map((rank) => (
            <p key={rank.news_id}>
              {rank.importance.title}: {rank.importance.score}/10. Влияние: {rank.impact.score}.{' '}
              {rank.impact.rationale}
            </p>
          ))}
          {analysis.warnings.map((warning, i) => (
            <p key={i}>{warning}</p>
          ))}
          {analysis.presentation.blocks.map((block) => (
            <section key={block.id}>
              <h3>{block.title}</h3>
              {block.empty_reason && <p>{block.empty_reason}</p>}
              {block.statement_ids.map((id) => {
                const row = statements.get(id);
                return (
                  row && (
                    <p key={id}>
                      <strong>{row.label}: </strong>
                      {row.statement.text}{' '}
                      {row.statement.source_ids.map((source) => (
                        <a key={source} href={`#source-${source}`}>
                          [{source}]{' '}
                        </a>
                      ))}
                    </p>
                  )
                );
              })}
              {block.id === 'basis' &&
                analysis.presentation.originals.map((source) => (
                  <details id={`source-${source.id}`} key={source.id}>
                    <summary>{source.title}</summary>
                    {source.quote && <blockquote>{source.quote}</blockquote>}
                    {source.url && (
                      <a href={source.url} target="_blank" rel="noreferrer">
                        Первоисточник
                      </a>
                    )}
                  </details>
                ))}
            </section>
          ))}
          <button disabled={busy} onClick={() => void showTrace()}>
            Показать шаги анализа
          </button>
          {trace && (
            <ol>
              {trace.steps.map((step) => (
                <li key={step.number}>
                  {step.goal} {step.tool && `(${step.tool})`} — {step.status}
                </li>
              ))}
            </ol>
          )}
        </section>
      )}
    </main>
  );
}
