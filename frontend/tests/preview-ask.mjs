import { createServer as createHttpServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { createServer as createViteServer } from 'vite';

const root = fileURLToPath(new URL('..', import.meta.url));
const profile = JSON.parse(
    await readFile(
        new URL(
            '../../docs/schemas/examples/company_profile.json',
            import.meta.url,
        ),
        'utf8',
    ),
);
const newsId = 'news:ask-preview';
const snapshot = 'snapshot:ask-preview';
const source = {
    id: 's1',
    type: 'news',
    ref_id: newsId,
    title: 'Учебный материал о поставках',
    quote: 'Учебный пример: поставщик увеличил срок доставки с 7 до 12 дней.',
};
const row = (id, text, origin, label) => ({
    statement: { id, text, origin, source_ids: ['s1'] },
    source_ids: ['s1'],
    label,
});

function insight(company, body) {
    const fact = row('fact', source.quote, 'fact', 'Факт · учебный пример');
    const meaning = row(
        'meaning',
        body.focus
            ? 'Учебный ответ: увеличение срока поставки может увеличить запас и потребность в оборотных средствах. Проверьте платёжный календарь на ближайшие две недели.'
            : 'Учебный пример: более длинная поставка может увеличить складской запас и расходы на закупки.',
        'model',
        'Вывод ИИ · пример',
    );
    const action = {
        ...row(
            'action',
            'Учебный пример действия: сравните запас, сроки и стоимость двух сценариев поставки.',
            'model',
            'Действие · пример',
        ),
        statement: {
            ...row(
                'action',
                'Учебный пример действия: сравните запас, сроки и стоимость двух сценариев поставки.',
                'model',
                '',
            ).statement,
            kind: 'scenario',
        },
    };
    return {
        insight: {
            schema_version: '1.0',
            id: 'insight:preview:' + Date.now(),
            company_id: company,
            news_ids: body.news_ids,
            run_id: 'run:preview',
            created_at: new Date().toISOString(),
            status: 'ready',
            headline: 'Учебный разбор: поставка занимает больше времени',
            significance: 'high',
            relevance: 0.8,
            what_happened: [fact.statement],
            why_it_matters: [meaning.statement],
            actions: [action.statement],
            sources: [source],
            self_check: { passed: true, iterations: 1, issues: [] },
        },
        presentation: {
            facts: [fact],
            bank_position: [],
            ai_conclusions: [meaning, action],
            client_context: [],
            originals: [source],
            blocks: [
                { id: 'event', title: 'Что произошло', statement_ids: ['fact'] },
                {
                    id: 'relevance',
                    title: 'Что это значит для компании',
                    statement_ids: ['meaning'],
                },
                {
                    id: 'consequences',
                    title: 'Риски и возможности',
                    statement_ids: [],
                    empty_reason: 'Учебный сценарий для проверки чата',
                },
                { id: 'actions', title: 'Что можно сделать', statement_ids: ['action'] },
                {
                    id: 'bank',
                    title: 'Позиция банка',
                    statement_ids: [],
                    empty_reason: 'В учебном сценарии банковской аналитики нет',
                },
                { id: 'basis', title: 'Источники', statement_ids: [] },
            ],
        },
        role: body.role ?? null,
        feed_snapshot_id: body.feed_snapshot_id ?? null,
        cache_hit: false,
        execution_status: 'completed',
        warnings: [],
        usage: { calls: 0, prompt_tokens: 0, completion_tokens: 0, known_cost_rub: '0' },
        disclaimer:
            'Учебные данные для просмотра интерфейса. Ответ не получен от LLM и не является инвестиционной рекомендацией.',
    };
}

const api = createHttpServer(async (request, response) => {
    const send = (status, value) => {
        response.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' });
        response.end(JSON.stringify(value));
    };
    try {
        const url = new URL(request.url, 'http://localhost');
        const company = decodeURIComponent(url.pathname.split('/')[3] ?? '');
        if (request.method === 'GET' && url.pathname.endsWith('/profile'))
            return send(200, {
                ...profile,
                company_id: company,
                legend: {
                    name: 'Учебная компания',
                    description: 'Данные для просмотра интерфейса',
                },
            });
        if (request.method === 'GET' && url.pathname.endsWith('/feed'))
            return send(200, {
                company_id: company,
                snapshot_id: snapshot,
                hidden_below_threshold: 0,
                items: [
                    {
                        news_id: newsId,
                        kind: 'news',
                        card: {
                            title: 'Пример новости: срок поставки увеличился',
                            source: 'Учебный источник',
                            published_at: new Date().toISOString(),
                        },
                        event: { type: 'Поставки', polarity: 'risk', markers: [] },
                        importance: { score: 8, level: 'important', title: 'Важно' },
                        impact: { score: -25, title: 'Умеренный риск', confidence: 'high' },
                        coverage: 1,
                        explanation: 'Учебная новость для проверки уточняющих вопросов.',
                    },
                ],
            });
        if (request.method === 'POST' && url.pathname.endsWith('/insights')) {
            let text = '';
            for await (const chunk of request) {
                text += chunk;
                if (text.length > 10000) return send(413, { detail: 'Слишком большой запрос' });
            }
            const body = JSON.parse(text);
            await delay(body.focus ? 1200 : 300);
            return send(200, insight(company, body));
        }
        if (request.method === 'GET' && url.pathname.endsWith('/trace'))
            return send(200, {
                steps: [
                    {
                        number: 1,
                        goal: 'Учебный сценарий без обращения к LLM',
                        status: 'completed',
                    },
                ],
            });
        return send(404, { detail: 'В учебном стенде этот маршрут не реализован' });
    } catch {
        return send(400, { detail: 'Некорректный запрос к учебному стенду' });
    }
});
await new Promise((resolve, reject) => {
    api.once('error', reject);
    api.listen(0, '127.0.0.1', resolve);
});
const address = api.address();
let web;
try {
    web = await createViteServer({
        root,
        cacheDir: join(tmpdir(), 'news-insights-ask-preview-vite'),
        server: {
            host: '127.0.0.1',
            port: 5174,
            strictPort: true,
            proxy: { '/api': { target: `http://127.0.0.1:${address.port}`, changeOrigin: true } },
        },
    });
    await web.listen();
} catch (error) {
    api.close();
    throw error;
}
console.log('Учебный стенд A6-06: http://127.0.0.1:5174/feed');
console.log(
    'Войдите без пароля, откройте пример новости и нажмите «Задать вопрос». Ответы учебные, запросов к LLM нет.',
);
for (const signal of ['SIGINT', 'SIGTERM'])
    process.once(signal, async () => {
        await web.close();
        api.close();
    });
