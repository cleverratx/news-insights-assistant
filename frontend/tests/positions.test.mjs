import assert from 'node:assert/strict';
import { before, after, test } from 'node:test';
import { createServer } from 'vite';

let server, transport, model;
before(async () => {
    server = await createServer({ server: { middlewareMode: true, watch: null, ws: false }, logLevel: 'error' });
    transport = await server.ssrLoadModule('/src/shared/api/position-context.ts');
    model = await server.ssrLoadModule('/src/features/positions/model.ts');
});
after(async () => { await server?.close(); });
const id = 'position:' + 'a'.repeat(32);
const search = `?company=one&role=logistics&position=${id}`;
test('контекст должности попадает в ленту, разбор и сводку выбранной компании', () => {
    const feed = transport.withPosition('/companies/one/feed?role=logistics&days=3', null, search);
    assert.equal(new URLSearchParams(feed.split('?')[1]).get('position'), id);
    assert.ok(transport.withPosition('/companies/one/insights', 'logistics', search).includes('position='));
    assert.ok(transport.withPosition('/companies/one/digest', 'logistics', search).includes('position='));
});
test('другие компании, должности, общий разрез и база знаний не получают личный контекст', () => {
    for (const [path, role] of [['/companies/two/feed', 'logistics'], ['/companies/one/insights', 'cfo'], ['/companies/one/feed', null], ['/companies/one/knowledge', 'logistics']]) {
        assert.equal(transport.withPosition(path, role, search), path);
    }
    assert.equal(transport.withPosition('/companies/one/feed', 'logistics', search.replace(id, 'constructor')), '/companies/one/feed');
});
test('название и контекст обязательны, ссылка сохраняет компанию и основу подбора', () => {
    assert.equal(model.validPosition('  ', 'Контекст'), false);
    assert.equal(model.validPosition('Закупки', '  '), false);
    assert.equal(model.validPosition('Закупки', 'я'.repeat(2001)), false);
    assert.equal(model.validPosition('Директор по закупкам', 'Работа с поставщиками'), true);
    const params = new URLSearchParams(model.positionSearch({ id, company_id: 'one', base_role: 'logistics' }));
    assert.equal(params.get('position'), id);
    assert.equal(params.get('role'), 'logistics');
});
