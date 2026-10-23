import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { createServer } from 'vite';

let server;
let conversation;

before(async () => {
    server = await createServer({
        server: { middlewareMode: true, ws: false, watch: null },
        appType: 'custom',
        logLevel: 'error',
    });
    conversation = await server.ssrLoadModule('/src/features/ask/conversation.ts');
});

after(async () => {
    await server?.close();
});

function original() {
    return {
        insight: {
            id: 'insight:one',
            company_id: 'company:one',
            news_ids: ['news:one', 'news:two'],
            headline: 'Событие для компании',
        },
        role: 'cfo',
        feed_snapshot_id: 'snapshot:one',
        presentation: {
            ai_conclusions: [{ statement: { text: 'Поставка ускорится на три дня.' } }],
        },
    };
}

test('вопрос сохраняет компанию, все события, должность и исходный снимок', () => {
    const result = original();
    result.related = { company: { id: 'counterparty:one' } };
    const request = conversation.questionRequest(result, 'Как изменятся расходы?');
    assert.deepEqual(request, {
        news_ids: ['news:one', 'news:two'],
        role: 'cfo',
        feed_snapshot_id: 'snapshot:one',
        related_company_id: 'counterparty:one',
        focus: 'Как изменятся расходы?',
        refresh: false,
    });
    request.news_ids.pop();
    assert.equal(result.insight.news_ids.length, 2);
});

test('вся компания и разбор без снимка не подменяются текущими настройками ленты', () => {
    const result = original();
    delete result.role;
    delete result.feed_snapshot_id;
    const request = conversation.questionRequest(result, 'Какие риски?');
    assert.equal(request.role, null);
    assert.equal(request.feed_snapshot_id, null);
    assert.equal(request.related_company_id, null);
});

test('первый вопрос получает исходный вывод, следующий — последний успешный обмен', () => {
    const result = original();
    assert.match(
        conversation.questionFocus('  Какие риски?  ', result, []),
        /Какие риски\?[\s\S]*Поставка ускорится/,
    );
    const answer = original();
    answer.presentation.ai_conclusions[0].statement.text = 'Проверьте резерв ликвидности.';
    const turns = [
        { id: 1, question: 'Что с расходами?', status: 'answered', answer },
        { id: 2, question: 'Неотправленный вопрос', status: 'error' },
    ];
    const focus = conversation.questionFocus('А что сделать сначала?', result, turns);
    assert.match(focus, /А что сделать сначала\?/);
    assert.match(focus, /Что с расходами\?/);
    assert.match(focus, /Проверьте резерв ликвидности/);
    assert.doesNotMatch(focus, /Неотправленный вопрос/);
});

test('600 символов вопроса не обрезаются ради истории, Unicode не повреждается', () => {
    const result = original();
    const longQuestion = 'я'.repeat(600);
    assert.equal(conversation.questionFocus(longQuestion, result, []), longQuestion);
    for (const size of [1, 200, 500, 550, 590, 600]) {
        const question = '📦'.repeat(size);
        const focus = conversation.questionFocus(question, result, []);
        assert.ok(Array.from(focus).length <= 600);
        assert.ok(focus.includes(question));
        assert.equal(focus.isWellFormed(), true);
    }
});

test('чужой ответ отклоняется по каждой части контекста, порядок событий не важен', () => {
    const result = original();
    assert.equal(conversation.sameContext(result, structuredClone(result)), true);
    const reordered = structuredClone(result);
    reordered.insight.news_ids.reverse();
    assert.equal(conversation.sameContext(result, reordered), true);
    for (const change of [
        (reply) => {
            reply.insight.company_id = 'company:another';
        },
        (reply) => {
            reply.insight.news_ids = ['news:another'];
        },
        (reply) => {
            reply.role = 'ceo';
        },
        (reply) => {
            reply.feed_snapshot_id = 'snapshot:another';
        },
        (reply) => {
            reply.related = { company: { id: 'counterparty:another' } };
        },
    ]) {
        const reply = structuredClone(result);
        change(reply);
        assert.equal(conversation.sameContext(result, reply), false);
    }
});

test('причина нехватки данных сохраняется в контексте следующего вопроса', () => {
    const result = original();
    const answer = original();
    answer.presentation.ai_conclusions = [];
    answer.insight.status_reason = 'Нужна доля импортных закупок.';
    const focus = conversation.questionFocus('Где указать эту долю?', result, [
        { id: 1, question: 'Как оценить валютный риск?', status: 'answered', answer },
    ]);
    assert.match(focus, /Нужна доля импортных закупок/);
    assert.match(focus, /Где указать эту долю/);
});
