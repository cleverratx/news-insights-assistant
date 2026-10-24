import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { createServer } from 'vite';

let server;
let model;

before(async () => {
    server = await createServer({
        server: { middlewareMode: true, watch: null, ws: false },
        logLevel: 'error',
    });
    model = await server.ssrLoadModule('/src/pages/competitors/model.ts');
});

after(async () => {
    await server?.close();
});

const mention = { id: 'competitor/one', news_id: 'publication-two', relationship: 'competitor' };

function response() {
    return {
        insight: { company_id: 'our-company', news_ids: ['publication-two'] },
        role: 'cfo',
        related: { company: { id: mention.id }, digest: ['fact'], meaning: ['conclusion'] },
        presentation: {
            facts: [{ statement: { id: 'fact', text: 'Событие у конкурента' } }],
            ai_conclusions: [{ statement: { id: 'conclusion', text: 'Последствие для нас' } }],
        },
    };
}

test('фильтры восстанавливаются из ссылки, неизвестные значения не попадают в запрос', () => {
    assert.deepEqual(
        model.readRelatedFilters(new URLSearchParams('scope=counterparties&days=14')),
        {
            scope: 'counterparties',
            days: 14,
        },
    );
    for (const query of [
        'scope=constructor&days=0',
        'scope=__proto__&days=-1',
        'scope=unknown&days=30',
    ]) {
        assert.deepEqual(model.readRelatedFilters(new URLSearchParams(query)), {
            scope: 'all',
            days: 7,
        });
    }
    const url = new URL(model.relatedFeedPath('company/one', 'competitors', 3), 'http://localhost');
    assert.equal(url.pathname, '/companies/company%2Fone/related-feed');
    assert.equal(url.searchParams.get('scope'), 'competitors');
    assert.equal(url.searchParams.get('days'), '3');
    assert.equal(url.searchParams.get('limit'), '50');
});

test('разбор относится к нашей компании, выбранному участнику и публикации с упоминанием', () => {
    assert.equal(model.relatedContextMatches(response(), 'our-company', mention, 'cfo'), true);
    for (const change of [
        (answer) => {
            answer.insight.company_id = 'other-company';
        },
        (answer) => {
            answer.insight.news_ids = ['representative-without-mention'];
        },
        (answer) => {
            answer.insight.news_ids.push('another-publication');
        },
        (answer) => {
            answer.related.company.id = 'other-competitor';
        },
        (answer) => {
            delete answer.related;
        },
        (answer) => {
            answer.role = 'ceo';
        },
        (answer) => {
            answer.feed_snapshot_id = 'snapshot-from-main-feed';
        },
    ]) {
        const answer = response();
        change(answer);
        assert.equal(model.relatedContextMatches(answer, 'our-company', mention, 'cfo'), false);
    }
});

test('режим всей компании не подменяется должностью пользователя', () => {
    const answer = response();
    delete answer.role;
    assert.equal(model.relatedContextMatches(answer, 'our-company', mention, null), true);
    assert.equal(model.relatedContextMatches(answer, 'our-company', mention, 'cfo'), false);
});

test('выжимка и последствия используют разные утверждения, пропуски и повторы безопасны', () => {
    const answer = response();
    answer.related.digest.push('missing', 'fact');
    answer.related.meaning.push('blank');
    answer.presentation.facts.push({ statement: { id: 'blank', text: '  ' } });
    assert.deepEqual(
        model.relatedStatements(answer, 'digest').map((row) => row.statement.text),
        ['Событие у конкурента'],
    );
    assert.deepEqual(
        model.relatedStatements(answer, 'meaning').map((row) => row.statement.text),
        ['Последствие для нас'],
    );
    answer.related.meaning = [];
    assert.deepEqual(model.relatedStatements(answer, 'meaning'), []);
});

test('подписи различают конкурента и подтверждённую роль контрагента', () => {
    assert.equal(model.mentionTitle(mention), 'Конкурент');
    assert.equal(
        model.mentionTitle({ relationship: 'counterparty', role: 'supplier' }),
        'Поставщик',
    );
    assert.equal(model.mentionTitle({ relationship: 'counterparty', role: null }), 'Контрагент');
});
