import { describe, expect, it, vi } from 'vitest';

import { followStock, listStocks, unfollowStock } from '@/lib/stocks';

const ok = (body: unknown): Response =>
  new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  });

function stub(response: Response): ReturnType<typeof vi.fn> {
  const mock = vi.fn(async () => response);
  vi.stubGlobal('fetch', mock);
  return mock;
}

const call = (mock: ReturnType<typeof vi.fn>): [string, RequestInit] =>
  mock.mock.calls[0] as unknown as [string, RequestInit];

describe('the stocks API calls', () => {
  it('lists the stocks from GET /api/v1/stocks and returns the items', async () => {
    const item = {
      symbol: 'DEMOA',
      name: 'DemoCo',
      bse_code: '000001',
      sector: 'S',
      followed: false,
    };
    const mock = stub(ok({ items: [item] }));

    await expect(listStocks()).resolves.toEqual([item]);
    expect(call(mock)[0]).toBe('/api/v1/stocks');
    expect(call(mock)[1].method).toBe('GET');
  });

  it('rejects a response that is not a list of items', async () => {
    stub(ok({ unexpected: true }));
    await expect(listStocks()).rejects.toMatchObject({ code: 'unexpected_response' });
  });

  it('follows with an idempotent PUT to the stock follow resource', async () => {
    const mock = stub(new Response(null, { status: 204 }));
    await followStock('DEMOA');

    expect(call(mock)[0]).toBe('/api/v1/stocks/DEMOA/follow');
    expect(call(mock)[1].method).toBe('PUT');
  });

  it('unfollows with DELETE to the same resource', async () => {
    const mock = stub(new Response(null, { status: 204 }));
    await unfollowStock('DEMOA');

    expect(call(mock)[0]).toBe('/api/v1/stocks/DEMOA/follow');
    expect(call(mock)[1].method).toBe('DELETE');
  });

  it('encodes symbols that contain reserved characters (real tickers include & and -)', async () => {
    const mock = stub(new Response(null, { status: 204 }));
    await followStock('M&M');
    expect(call(mock)[0]).toBe('/api/v1/stocks/M%26M/follow');
  });

  it('cannot be pointed at another path through the symbol', async () => {
    const mock = stub(new Response(null, { status: 204 }));
    await followStock('../../auth/logout');
    expect(call(mock)[0]).toBe('/api/v1/stocks/..%2F..%2Fauth%2Flogout/follow');
  });
});
