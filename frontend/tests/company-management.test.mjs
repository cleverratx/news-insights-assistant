import assert from 'node:assert/strict';
import { before, after, afterEach, test } from 'node:test';
import { createServer } from 'vite';

let server, api, model;
const originalFetch = globalThis.fetch;
before(async () => {
    server = await createServer({ server: { middlewareMode: true, watch: null, ws: false }, logLevel: 'error' });
    api = await server.ssrLoadModule('/src/shared/api/client.ts');
    model = await server.ssrLoadModule('/src/features/company-management/model.ts');
});
afterEach(() => { globalThis.fetch = originalFetch; });
after(async () => { await server?.close(); });

test('успешное удаление с пустым ответом 204 не требует JSON и сохраняет сигнал отмены', async () => {
    let request;
    const controller = new AbortController();
    globalThis.fetch = async (path, init) => { request = { path, init }; return new Response(null, { status: 204 }); };
    assert.equal(await api.apiDelete('/companies/one/links/manual%3Aone', controller.signal), undefined);
    assert.equal(request.path, '/api/companies/one/links/manual%3Aone');
    assert.equal(request.init.method, 'DELETE');
    assert.equal(request.init.signal, controller.signal);
});

test('ошибка удаления сохраняет статус и сообщение сервера для повторной попытки', async () => {
    globalThis.fetch = async () => new Response(JSON.stringify({ detail: 'Факт изменён другим пользователем' }), { status: 409 });
    await assert.rejects(api.apiDelete('/companies/one/knowledge/facts/one'), error => error instanceof api.ApiError && error.status === 409 && error.message === 'Факт изменён другим пользователем');
});

test('удаление связи разрешено только компании, добавленной клиентом', () => {
    assert.equal(model.isManual({ company_id: null, origins: ['client'] }), true);
    assert.equal(model.isManual({ company_id: 'bank-one', origins: ['payments'] }), false);
    assert.equal(model.isManual({ company_id: 'bank-one', origins: ['client','payments'] }), false);
    assert.equal(model.isManual({ company_id: null, origins: ['group'] }), false);
});

test('добавление контрагента сохраняет роль и дополнительные сведения без пустых полей', () => {
    assert.deepEqual(model.linkBody('  Перевозчик  ', 'carrier', ' Транспорт ', ' 49.20 ', ' Срочные поставки '), {
        name: 'Перевозчик', role: 'carrier', industry: 'Транспорт', okved_code: '49.20', note: 'Срочные поставки',
    });
    assert.deepEqual(model.linkBody('Поставщик', 'supplier', ' ', '', ''), { name: 'Поставщик', role: 'supplier' });
    assert.equal(model.validName('  '), false);
    assert.equal(model.validName('я'.repeat(301)), false);
    assert.equal(model.validName('🟢'.repeat(300)), true);
    assert.equal(model.collectionPath('one/two','competitors','wikidata:Q1/2'), '/companies/one%2Ftwo/competitors/wikidata%3AQ1%2F2');
});
