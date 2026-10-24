import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { createServer } from 'vite';

let server;
let model;
before(async () => {
    server = await createServer({
        server: { middlewareMode: true, watch: null, ws: false }, logLevel: 'error',
    });
    model = await server.ssrLoadModule('/src/features/knowledge/model.ts');
});
after(async () => { await server?.close(); });

test('сначала важные вопросы, известные и отвеченные темы не повторяются', () => {
    const questions = [
        { key: 'plans.next_year', priority: 'low' },
        { key: 'products.main', priority: 'medium' },
        { key: 'seasonality.peak', priority: 'high' },
        { key: 'seasonality.peak', priority: 'high' },
        { key: 'markets.sales_regions', priority: 'high' },
    ];
    const facts = [{ key: 'products.main', deleted: false }];
    assert.deepEqual(model.unansweredQuestions(questions, facts, ['plans.next_year']).map(q => q.key), ['seasonality.peak', 'markets.sales_regions']);
    assert.equal(questions.length, 5);
    assert.equal(model.unansweredQuestions(questions, [{ key: 'products.main', deleted: true }], []).length, 4);
});

test('быстрые ответы содержат самостоятельный факт, ложное значение не теряется', () => {
    const noSeason = model.quickAnswers('seasonality.peak')[0];
    assert.equal(noSeason.body.value, false);
    assert.ok(noSeason.body.text.includes('сезонности'));
    assert.ok(model.quickAnswers('constraints.main').every(answer => answer.body.text.includes('ограничение')));
    assert.deepEqual(model.quickAnswers('new.question'), []);
    assert.deepEqual(model.quickAnswers('constructor'), []);
});

test('текст обрезается по краям, пустой и превышающий лимит не отправляются', () => {
    assert.deepEqual(model.textAnswer('  Продаём в Казани  '), { text: 'Продаём в Казани' });
    assert.equal(model.textAnswer(' \n\t'), null);
    assert.equal(model.textAnswer('я'.repeat(2001)), null);
    assert.ok(model.textAnswer('🟢'.repeat(2000)));
});

test('чужой или неподтверждённый ответ не отмечает вопрос выполненным', () => {
    const fact = { company_id: 'one', key: 'seasonality.peak', source: 'client', status: 'confirmed', deleted: false };
    assert.equal(model.isAnswerFor(fact, 'one', 'seasonality.peak'), true);
    for (const change of [{ company_id: 'two' }, { key: 'plans.next_year' }, { source: 'system' }, { status: 'assumption' }, { deleted: true }]) {
        assert.equal(model.isAnswerFor({ ...fact, ...change }, 'one', 'seasonality.peak'), false);
    }
});

test('идентификаторы компании и вопроса безопасно кодируются в адресе', () => {
    assert.equal(model.questionPath('one/two', 'a/b?'), '/companies/one%2Ftwo/knowledge/questions/a%2Fb%3F/answer');
    assert.equal(model.factAnchor('fact:1'), 'knowledge-fact-fact:1');
});

test('два вопроса о рынках различимы в переключателе тем', () => {
    assert.notEqual(
        model.questionTitle({ key: 'markets.sales_regions', category: 'markets' }),
        model.questionTitle({ key: 'markets.export_share', category: 'markets' }),
    );
});
