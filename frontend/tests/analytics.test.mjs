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
    model = await server.ssrLoadModule('/src/pages/analytics/model.ts');
});
after(async () => {
    await server?.close();
});

function event(id, risk, opportunity, published, extra = {}) {
    return {
        news_id: id,
        story_id: `story:${id}`,
        significance: 'high',
        card: { published_at: published },
        impact: {
            scale: 'weighted',
            score: opportunity - risk,
            risk,
            opportunity,
            criteria: [],
            confidence: 'high',
        },
        event: { markers: ['fx'], topics: ['fx'] },
        ...extra,
    };
}
function feed(items) {
    return { company_id: 'one', generated_at: '2026-10-24T12:00:00Z', period_days: 7, items };
}

test('даты считаются по Москве, пропущенные дни остаются на шкале с нулём', () => {
    const item = event('a', 20, 0, '2026-10-22T22:30:00Z');
    const result = model.buildAnalytics(feed([item]));
    assert.equal(model.dateKey(item.card.published_at), '2026-10-23');
    assert.equal(result.days.find((day) => day.key === '2026-10-23').risk, 20);
    assert.equal(result.days.find((day) => day.key === '2026-10-22').count, 0);
    assert.equal(result.days[0].key, '2026-10-17');
    assert.equal(result.days.at(-1).key, '2026-10-24');
});

test('двоякое влияние не пропадает при нулевом итоге: обе стороны попадают в график и индикаторы', () => {
    const items = [
        event('risk', 20, 0, '2026-10-23T05:00:00Z'),
        event('mixed', 30, 30, '2026-10-23T06:00:00Z', { significance: 'medium' }),
        event('opportunity', 0, 40, '2026-10-24T06:00:00Z'),
    ];
    const result = model.buildAnalytics(feed(items));
    assert.deepEqual(result.directions, { risk: 1, opportunity: 1, mixed: 1, neutral: 0 });
    assert.equal(result.days.find((day) => day.key === '2026-10-23').risk, 50);
    assert.equal(result.days.find((day) => day.key === '2026-10-23').opportunity, 30);
    assert.equal(result.important, 2);
    assert.equal(result.riskCount, 2);
    assert.equal(result.opportunityCount, 2);
});

test('повторные публикации сюжета и повторные маркеры не раздувают статистику', () => {
    const first = event('a', 10, 0, '2026-10-23T05:00:00Z', {
        event: { markers: ['fx', 'fx', 'logistics'], topics: ['trade', 'fx'] },
    });
    const duplicate = { ...first, news_id: 'another-publication', significance: 'low' };
    const second = event('b', 0, 10, '2026-10-23T06:00:00Z', {
        event: { markers: [], topics: [] },
    });
    const result = model.buildAnalytics(feed([first, second, duplicate]));
    assert.deepEqual(
        result.items.map((item) => item.news_id),
        ['a', 'b'],
    );
    assert.equal(result.important, 2);
    assert.equal(result.markers.find((group) => group.key === 'fx').count, 1);
    assert.equal(result.markers.find((group) => group.key === 'unclassified').count, 1);
    assert.equal(result.topics.find((group) => group.key === 'trade').count, 1);
});

test('разные шкалы не складываются: график переключается на количество событий', () => {
    const old = event('old', 0, 0, '2026-10-23T05:00:00Z');
    old.impact = {
        scale: 'levels',
        score: 0,
        criteria: [],
        answers: [{ level: -1 }, { level: 1 }],
    };
    const result = model.buildAnalytics(feed([old, event('new', 80, 0, '2026-10-23T06:00:00Z')]));
    assert.equal(result.units, 'events');
    assert.equal(result.days.find((day) => day.key === '2026-10-23').risk, 2);
    assert.equal(result.days.find((day) => day.key === '2026-10-23').opportunity, 1);
    assert.equal(model.directionOf(old), 'mixed');
});

test('выбор дня, маркера, темы и характера влияния возвращает именно события графика', () => {
    const items = [
        event('a', 10, 0, '2026-10-22T22:30:00Z'),
        event('b', 0, 20, '2026-10-24T05:00:00Z', {
            event: { markers: ['logistics'], topics: ['trade'] },
        }),
    ];
    for (const selection of [
        { kind: 'day', key: '2026-10-23' },
        { kind: 'marker', key: 'fx' },
        { kind: 'topic', key: 'fx' },
        { kind: 'direction', key: 'risk' },
    ]) {
        assert.deepEqual(
            model.selectEvents(items, selection).map((item) => item.news_id),
            ['a'],
        );
    }
    assert.equal(model.selectEvents(items, null).length, 2);
});

test('пустая лента, недатированные и старые публикации обрабатываются без NaN и скрытой потери событий', () => {
    const empty = model.buildAnalytics(feed([]));
    assert.equal(empty.important, 0);
    assert.ok(empty.days.every((day) => day.risk === 0 && day.opportunity === 0));
    const result = model.buildAnalytics(
        feed([event('invalid', 10, 0, 'invalid'), event('old', 10, 0, '2020-01-01T00:00:00Z')]),
    );
    assert.equal(result.items.length, 2);
    assert.equal(result.unplotted, 2);
    assert.equal(JSON.stringify(result).includes('NaN'), false);
});
