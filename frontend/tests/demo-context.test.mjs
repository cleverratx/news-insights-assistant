import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { createServer } from 'vite';

let server;
let context;
let companies;
let profile;

before(async () => {
    server = await createServer({
        server: { middlewareMode: true, watch: null, ws: false },
        logLevel: 'error',
    });
    context = await server.ssrLoadModule('/src/features/demo/context.ts');
    companies = (await server.ssrLoadModule('/src/shared/lib/demo.ts')).DEMO_COMPANIES;
    profile = await server.ssrLoadModule('/src/features/demo/profile.ts');
});

after(async () => {
    await server?.close();
});

test('прямая ссылка восстанавливает компанию и должность, неизвестные значения безопасны', () => {
    assert.ok(companies.length >= 2);
    for (const company of companies) {
        for (const role of ['ceo', 'cfo', 'logistics', 'all']) {
            const selected = context.readDemoContext(new URLSearchParams({ company, role }), 'ceo');
            assert.equal(selected.company, company);
            assert.equal(selected.role, role === 'all' ? null : role);
            assert.equal(new URLSearchParams(selected.search).get('company'), company);
        }
    }
    for (const role of ['unknown', '__proto__', 'constructor', 'toString']) {
        const selected = context.readDemoContext(
            new URLSearchParams({ company: 'missing', role }),
            'cfo',
        );
        assert.equal(selected.company, companies[0]);
        assert.equal(selected.role, 'cfo');
    }
});

test('смена из разбора открывает ленту без чужого снимка и сохраняет условия сравнения', () => {
    const original = new URLSearchParams({
        company: companies[0],
        role: 'all',
        snapshot: 'snapshot-first-company',
        basis: '1',
        days: '14',
        marker: 'fx',
        polarity: 'risk',
        significance: 'high',
        all: '1',
        view: 'related',
    });
    const location = context.companyLocation('/news/news-one', original, companies[1], 'cfo');
    assert.equal(location.pathname, '/feed');
    const next = new URLSearchParams(location.search);
    assert.equal(next.get('company'), companies[1]);
    assert.equal(next.has('snapshot'), false);
    assert.equal(next.has('basis'), false);
    for (const key of ['role', 'days', 'marker', 'polarity', 'significance', 'all', 'view']) {
        assert.equal(next.get(key), original.get(key));
    }
    assert.equal(original.get('company'), companies[0]);
    assert.equal(original.get('snapshot'), 'snapshot-first-company');
});

test('главная и лента сохраняют маршрут; неявная должность записывается в ссылку', () => {
    for (const pathname of ['/', '/feed']) {
        const location = context.companyLocation(
            pathname,
            new URLSearchParams(),
            companies[1],
            'logistics',
        );
        assert.equal(location.pathname, pathname);
        assert.equal(new URLSearchParams(location.search).get('role'), 'logistics');
    }
});

test('повторный выбор и неизвестная компания не запускают переход и загрузку ленты', () => {
    const params = new URLSearchParams({ company: companies[0] });
    for (const selected of [companies[0], 'unknown', '__proto__']) {
        assert.equal(context.companyLocation('/news/one', params, selected, 'ceo'), null);
    }
});

test('особенности профиля берутся из API; отсутствие данных не превращается в выдуманный профиль', () => {
    assert.deepEqual(
        profile.profileHighlights({
            products: ['loan', 'leasing', 'payroll'],
            foreign_trade: { has_customs_records: true, countries: ['КИТАЙ', 'ТУРЦИЯ'] },
        }),
        ['Кредиты', 'Лизинг', 'ВЭД: КИТАЙ, ТУРЦИЯ'],
    );
    assert.deepEqual(
        profile.profileHighlights({ foreign_trade: { has_customs_records: false } }),
        [],
    );
    assert.deepEqual(profile.profileHighlights({ foreign_trade: { has_customs_records: true } }), [
        'Есть внешнеэкономическая деятельность',
    ]);
});
