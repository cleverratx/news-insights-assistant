import assert from 'node:assert/strict';
import { after, before, beforeEach, test } from 'node:test';
import { createServer } from 'vite';

let server;
let navigation;
let links;
const values = new Map();
const originalStorage = Object.getOwnPropertyDescriptor(globalThis, 'sessionStorage');
const storage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
};

before(async () => {
    Object.defineProperty(globalThis, 'sessionStorage', { configurable: true, value: storage });
    server = await createServer({
        server: { middlewareMode: true, watch: null, ws: false },
        logLevel: 'error',
    });
    navigation = await server.ssrLoadModule('/src/features/feed/navigation.ts');
    links = await server.ssrLoadModule('/src/features/insight/link.ts');
});

beforeEach(() => values.clear());

after(async () => {
    await server?.close();
    if (originalStorage) Object.defineProperty(globalThis, 'sessionStorage', originalStorage);
    else delete globalThis.sessionStorage;
});

const search = '?company=our-company&role=cfo&days=14&marker=fx';
const feed = {
    company_id: 'our-company',
    role: 'cfo',
    snapshot_id: 'snapshot-main',
    items: [{ news_id: 'news-main', story_id: 'story-one' }],
};
const event = {
    news_id: 'news-other-publication',
    story_id: 'story-one',
    publications: [{ news_id: 'news-other-publication' }],
};

test('кнопка ведёт по точной ссылке главной ленты, сохраняя снимок, роль и исходные фильтры', () => {
    navigation.rememberMainFeed(feed, search);
    const expected = links.newsLink(search, 'news-main', 'snapshot-main');
    assert.deepEqual(
        navigation.fullAnalysisLink('?company=our-company&role=cfo&days=7', event),
        expected,
    );
    const changedHome = { ...feed, snapshot_id: 'snapshot-home-new' };
    assert.deepEqual(navigation.fullAnalysisLink(search, event, changedHome), expected);
});

test('один сюжет и разные публикации открывают ту же карточку главной ленты', () => {
    navigation.rememberMainFeed(feed, search);
    const withoutStory = { ...event, story_id: null, publications: [{ news_id: 'news-main' }] };
    assert.equal(navigation.fullAnalysisLink(search, withoutStory).pathname, '/news/news-main');
});

test('снимки другой компании и должности не попадают в ссылку', () => {
    navigation.rememberMainFeed(feed, search);
    for (const context of ['?company=other-company&role=cfo', '?company=our-company&role=ceo']) {
        const link = navigation.fullAnalysisLink(context, event, feed);
        assert.equal(new URLSearchParams(link.search).has('snapshot'), false);
        assert.equal(link.pathname, '/news/news-other-publication');
    }
    const unrelated = { ...event, story_id: 'other-story', publications: [] };
    assert.equal(
        new URLSearchParams(navigation.fullAnalysisLink(search, unrelated, feed).search).has(
            'snapshot',
        ),
        false,
    );
});

test('до посещения главной ленты используется ссылка существующей карточки главного экрана', () => {
    assert.deepEqual(
        navigation.fullAnalysisLink(search, event, feed),
        links.newsLink(search, 'news-main', 'snapshot-main'),
    );
    navigation.rememberMainFeed(feed, search);
    navigation.clearFeedNavigation();
    assert.equal(
        new URLSearchParams(navigation.fullAnalysisLink(search, event).search).has('snapshot'),
        false,
    );
});

test('некорректное хранилище и запрет записи не мешают открыть новость', () => {
    for (const invalid of ['not-json', '{}', '[null, {"company": "our-company"}]']) {
        storage.setItem('alfa-feed-navigation', invalid);
        assert.deepEqual(
            navigation.fullAnalysisLink(search, event, feed),
            links.newsLink(search, 'news-main', 'snapshot-main'),
        );
    }
    Object.defineProperty(globalThis, 'sessionStorage', {
        configurable: true,
        get: () => {
            throw new Error('unavailable');
        },
    });
    try {
        navigation.rememberMainFeed(feed, search);
        assert.deepEqual(
            navigation.fullAnalysisLink(search, event, feed),
            links.newsLink(search, 'news-main', 'snapshot-main'),
        );
    } finally {
        Object.defineProperty(globalThis, 'sessionStorage', { configurable: true, value: storage });
    }
});
