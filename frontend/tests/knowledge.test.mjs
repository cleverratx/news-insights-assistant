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
    model = await server.ssrLoadModule('/src/pages/knowledge/model.ts');
});
after(async () => { await server?.close(); });

const fact = {
    id: 'draft:one:trade', company_id: 'one', version: 4, category: 'markets',
    text: 'Импортёр: закупки в Китае', value: 'import', key: 'markets.trade_direction',
    source: 'system', status: 'assumption', deleted: false,
};

test('поиск и фильтры пересекаются, ё и е равнозначны, удалённые факты скрыты', () => {
    const bank = { ...fact, id: 'bank', source: 'bank_data' };
    const client = { ...fact, id: 'client', source: 'client', status: 'confirmed' };
    const deleted = { ...fact, id: 'deleted', deleted: true };
    const filter = { ...model.EMPTY_FILTER, query: 'импортер кита', source: 'system', status: 'assumption', category: 'markets' };
    assert.deepEqual(model.filterFacts([fact, bank, client, deleted], filter), [fact]);
    assert.deepEqual(model.filterFacts([fact], { ...filter, status: 'confirmed' }), []);
    assert.equal(model.filterFacts([bank], { ...model.EMPTY_FILTER, query: 'данные банка' }).length, 1);
});

test('правка сохраняет исходную версию для защиты от параллельного изменения', () => {
    assert.deepEqual(model.editPayload(fact, '  Теперь экспортируем  ', 'markets', 'export'), {
        text: 'Теперь экспортируем', category: 'markets', value: 'export', expected_version: 4,
    });
    const latest = { ...fact, version: 5 };
    assert.equal(model.editPayload(latest, 'Мой текст', 'markets', 'both').expected_version, 5);
});

test('значения роли и конкурента сохраняют тип, неизвестный вариант не подменяет исходное значение', () => {
    const role = { ...fact, key: 'counterparty.two.role', value: 'buyer' };
    assert.equal(model.editPayload(role, 'Наш перевозчик', 'suppliers', 'carrier').value, 'carrier');
    const competitor = { ...fact, key: 'competitors.two', value: true };
    assert.equal(model.editPayload(competitor, 'Не конкурент', 'markets', 'false').value, false);
    assert.equal(model.editPayload(fact, 'Новая формулировка', 'markets', 'unknown').value, 'import');
});

test('новая формулировка не сохраняет противоречащее ей автоматическое значение', () => {
    const automatic = { ...fact, key: 'company.size', value: 'largest' };
    assert.equal(model.editPayload(automatic, 'Изменился масштаб компании', 'other', '').value, null);
    assert.equal(model.editPayload(automatic, automatic.text, 'other', '').value, 'largest');
});

test('пустой и слишком длинный текст не отправляется, Unicode считается как на сервере', () => {
    assert.equal(model.validText('  \n '), false);
    assert.equal(model.validText('я'.repeat(2001)), false);
    assert.equal(model.validText('🟢'.repeat(2000)), true);
    assert.equal(model.validText(' Факт '), true);
    assert.equal(model.factPath('one/two', 'факт:1/2'), '/companies/one%2Ftwo/knowledge/facts/%D1%84%D0%B0%D0%BA%D1%82%3A1%2F2');
});
