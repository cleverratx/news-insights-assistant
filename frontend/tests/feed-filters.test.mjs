import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { createServer } from 'vite';

let server;
let filters;
let feed;
let insight;

before(async () => {
    server = await createServer({
        configFile: 'vite.config.ts',
        server: { middlewareMode: true, watch: null, ws: false },
    });
    filters = await server.ssrLoadModule('/src/features/feed/filters.ts');
    feed = await server.ssrLoadModule('/src/shared/api/feed.ts');
    insight = await server.ssrLoadModule('/src/features/insight/link.ts');
});

after(async () => {
    await server?.close();
});

test('неизвестные фильтры и свойства прототипа не попадают в запрос, старые ссылки работают', () => {
    const defaults = filters.readFeedFilters(new URLSearchParams());
    for (const params of [
        'days=-1&marker=unknown&polarity=negative&significance=urgent',
        'days=30&marker=constructor&significance=toString',
        'days=abc&marker=__proto__',
    ]) {
        assert.deepEqual(filters.readFeedFilters(new URLSearchParams(params)), defaults);
    }
    assert.equal(filters.readFeedFilters(new URLSearchParams('important=1')).significance, 'high');
    assert.equal(
        filters.readFeedFilters(new URLSearchParams('important=1&significance=low')).significance,
        'low',
    );
});

test('сочетание риска, типа и значимости работает как пересечение', () => {
    const item = (id, polarity, marker, significance) => ({
        news_id: id,
        significance,
        event: { polarity, markers: [marker] },
    });
    const items = [
        item('fx-risk', 'risk', 'fx', 'high'),
        item('tax-risk', 'risk', 'regulation_taxes', 'high'),
        item('fx-chance', 'opportunity', 'fx', 'high'),
        item('fx-neutral', 'neutral', 'fx', 'high'),
        item('fx-background', 'risk', 'fx', 'low'),
    ];
    const selected = filters.readFeedFilters(
        new URLSearchParams('polarity=risk&marker=fx&significance=high'),
    );
    assert.deepEqual(
        filters.applyFilters(items, selected).map((item) => item.news_id),
        ['fx-risk'],
    );
    assert.equal(filters.applyFilters(items, { ...selected, polarity: null }).length, 3);
    assert.equal(filters.applyFilters(items, { ...selected, significance: 'medium' }).length, 0);
    assert.equal(items.length, 5);
});

test('полный сброс сохраняет компанию, должность, вкладку и посторонние параметры', () => {
    const original = new URLSearchParams(
        'company=company-one&role=cfo&view=related&polarity=risk&marker=fx&important=1&days=14&all=1&custom=yes',
    );
    const reset = filters.updateFilterParams(original, {
        polarity: null,
        marker: null,
        significance: null,
        days: filters.DEFAULT_DAYS,
        all: false,
    });
    assert.equal(filters.hasActiveFilters(filters.readFeedFilters(reset)), false);
    assert.equal(reset.get('company'), 'company-one');
    assert.equal(reset.get('role'), 'cfo');
    assert.equal(reset.get('view'), 'related');
    assert.equal(reset.get('custom'), 'yes');
    assert.equal(reset.has('important'), false);
    assert.equal(original.get('days'), '14');
});

test('тип, период и значимость отправляются в API до ограничения выдачи', () => {
    for (const significance of ['high', 'medium', 'low']) {
        const path = feed.feedPath('company/one', 'cfo', {
            days: 14,
            marker: 'fx',
            significance,
            limit: feed.FEED_LIMIT,
        });
        const url = new URL(path, 'http://localhost');
        assert.equal(url.pathname, '/companies/company%2Fone/feed');
        assert.equal(url.searchParams.get('days'), '14');
        assert.equal(url.searchParams.get('markers'), 'fx');
        assert.equal(url.searchParams.get('significance'), significance);
        assert.equal(url.searchParams.get('min_importance'), '0');
        assert.equal(url.searchParams.get('limit'), '50');
        assert.equal(url.searchParams.get('role'), 'cfo');
    }
    assert.equal(feed.feedPath('one', null), '/companies/one/feed');
});

test('переход в разбор и обратная ссылка сохраняют фильтры и исходный снимок', () => {
    const search = '?company=one&role=cfo&days=14&marker=fx&polarity=risk&significance=low&all=1';
    const link = insight.newsLink(search, 'news/one', 'snapshot-one');
    assert.equal(link.pathname, '/news/news%2Fone');
    const params = new URLSearchParams(link.search);
    assert.equal(params.get('snapshot'), 'snapshot-one');
    params.set('basis', '1');
    const back = new URLSearchParams(filters.feedSearch(params));
    assert.deepEqual(
        filters.readFeedFilters(back),
        filters.readFeedFilters(new URLSearchParams(search)),
    );
    assert.equal(back.get('company'), 'one');
    assert.equal(back.get('role'), 'cfo');
    assert.equal(back.has('snapshot'), false);
    assert.equal(back.has('basis'), false);
});
