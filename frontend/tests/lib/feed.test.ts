import { afterEach, describe, expect, it, vi } from 'vitest';

import { getFeed } from '@/lib/feed';
import { rbiUrl } from '@/lib/documents';
import { demoFeedItem, FEED_ATTRIBUTION } from '../helpers/fakeApi';

afterEach(() => vi.unstubAllGlobals());

const serve = (body: unknown): ReturnType<typeof vi.fn> => {
  const mock = vi.fn(
    async () =>
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
  );
  vi.stubGlobal('fetch', mock);
  return mock;
};

describe('getFeed', () => {
  it('reads GET /api/v1/feed', async () => {
    const mock = serve({ items: [demoFeedItem()], attribution: FEED_ATTRIBUTION });
    const feed = await getFeed();
    expect(mock.mock.calls[0]?.[0]).toBe('/api/v1/feed');
    expect(feed.items).toEqual([demoFeedItem()]);
    expect(feed.attribution).toBe(FEED_ATTRIBUTION);
  });

  it('accepts an empty list and null date and url', async () => {
    serve({ items: [], attribution: 'x' });
    expect((await getFeed()).items).toEqual([]);
    serve({
      items: [demoFeedItem({ published_at: null, url: null, is_fixture: true })],
      attribution: 'x',
    });
    expect((await getFeed()).items).toHaveLength(1);
  });

  it.each([
    ['null body', null],
    ['no items', { attribution: 'x' }],
    ['items not a list', { items: {}, attribution: 'x' }],
    ['no attribution', { items: [] }],
    ['attribution not text', { items: [], attribution: 3 }],
    ['an item that is null', { items: [null], attribution: 'x' }],
    ['a missing title', { items: [{ ...demoFeedItem(), title: undefined }], attribution: 'x' }],
    ['a numeric date', { items: [{ ...demoFeedItem(), published_at: 5 }], attribution: 'x' }],
    ['a numeric summary', { items: [{ ...demoFeedItem(), summary: 5 }], attribution: 'x' }],
    ['a numeric url', { items: [{ ...demoFeedItem(), url: 5 }], attribution: 'x' }],
    [
      'a non-boolean fixture flag',
      { items: [{ ...demoFeedItem(), is_fixture: 'no' }], attribution: 'x' },
    ],
  ])('rejects %s', async (_label, body) => {
    serve(body);
    await expect(getFeed()).rejects.toMatchObject({ code: 'unexpected_response' });
  });
});

describe('rbiUrl', () => {
  it.each([
    'https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=1',
    'https://rbi.org.in/Scripts/x.aspx',
  ])('accepts %s', (url) => {
    expect(rbiUrl(url)).toBe(url);
  });

  it.each([
    ['null', null],
    ['plain http', 'http://www.rbi.org.in/x'],
    ['another host', 'https://www.example.org/x'],
    ['a look-alike suffix', 'https://rbi.org.in.evil.example/x'],
    ['a look-alike prefix', 'https://www.rbi.org.in.evil.example/x'],
    ['a look-alike name', 'https://evilrbi.org.in/x'],
    ['user info trick', 'https://www.rbi.org.in@evil.example/x'],
    ['a subdomain', 'https://evil.rbi.org.in/x'],
    ['javascript:', 'javascript:alert(1)'],
    ['not a URL', 'not a url'],
    ['empty', ''],
  ])('rejects %s', (_label, url) => {
    expect(rbiUrl(url)).toBeNull();
  });
});
