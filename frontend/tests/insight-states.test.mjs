import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { createServer } from 'vite';

let server;
let states;

before(async () => {
    server = await createServer({
        server: { middlewareMode: true, watch: null, ws: false },
        logLevel: 'error',
    });
    states = await server.ssrLoadModule('/src/features/insight/result-state.ts');
});

after(async () => {
    await server?.close();
});

function result() {
    return {
        insight: { status: 'ready' },
        execution_status: 'completed',
        presentation: {
            facts: [{ statement: { id: 'fact', text: 'Подтверждённый факт' } }],
            blocks: [{ id: 'event', statement_ids: ['fact'] }],
        },
    };
}

test('готовый ответ содержит текст, на который ссылаются видимые блоки', () => {
    const complete = result();
    assert.equal(states.insightResultState(complete), 'ready');
    assert.equal(states.hasInsightContent(complete), true);
});

test('пустые блоки, отсутствующие утверждения и пробелы не выглядят готовым разбором', () => {
    for (const change of [
        (answer) => {
            answer.presentation.blocks = [];
        },
        (answer) => {
            answer.presentation.blocks[0].statement_ids = ['missing'];
        },
        (answer) => {
            answer.presentation.facts[0].statement.text = '  \n ';
        },
        (answer) => {
            answer.presentation.blocks[0].id = 'basis';
        },
    ]) {
        const answer = result();
        change(answer);
        assert.equal(states.insightResultState(answer), 'empty');
        assert.equal(states.hasInsightContent(answer), false);
    }
});

test('прерывание обработки сохраняет частичный результат независимо от количества текста', () => {
    for (const execution of [
        'step_limit',
        'timeout',
        'planner_error',
        'check_failed',
        'unavailable',
    ]) {
        const partial = result();
        partial.execution_status = execution;
        assert.equal(states.insightResultState(partial), 'partial');
        partial.presentation.facts = [];
        assert.equal(states.insightResultState(partial), 'partial');
    }
});

test('нехватка данных и отсутствие связи с компанией не превращаются в техническую ошибку', () => {
    const answer = result();
    answer.insight.status = 'insufficient_data';
    answer.execution_status = 'timeout';
    assert.equal(states.insightResultState(answer), 'insufficient');
    answer.insight.status = 'no_significant_link';
    assert.equal(states.insightResultState(answer), 'unrelated');
});

test('предложение банка считается содержимым только в блоке действий', () => {
    const answer = result();
    answer.presentation.facts = [];
    answer.offer = { title: 'Обсудить условия с менеджером' };
    assert.equal(states.hasInsightContent(answer), false);
    answer.presentation.blocks = [{ id: 'actions', statement_ids: [] }];
    assert.equal(states.hasInsightContent(answer), true);
});
